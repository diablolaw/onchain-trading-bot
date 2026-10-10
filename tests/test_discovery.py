import copy
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
from argparse import Namespace

from desk.core import Desk, Rules
from desk.discovery import GeckoTerminal, parse_pool, queue
from desk.ai_cli import run
from test_desk import NOW
from test_preflight import key


def pool(index=1,age=3600,volume='100000',cap='1000000'):
    return dict(type='pool',id='solana_'+key(index),attributes=dict(address=key(index),
        pool_created_at=datetime.fromtimestamp(NOW-age,timezone.utc).isoformat(),reserve_in_usd='50000',
        volume_usd=dict(h24=volume),market_cap_usd=cap),
        relationships=dict(base_token=dict(data=dict(type='token',id='solana_'+key(index+100)))))


class DiscoveryTests(unittest.TestCase):
    def test_two_pages_deduplicate_pools_without_alphabetic_token_cut(self):
        http,recorder = Mock(),Mock()
        http.request.side_effect = [(dict(data=[pool(2),pool(1)]),NOW),(dict(data=[pool(1),pool(3)]),NOW)]
        found = GeckoTerminal(recorder,http).new_pools()
        self.assertEqual([p['token'] for p in found],[key(102),key(101),key(103)])
        self.assertEqual(http.request.call_count,2)
        self.assertIn('/networks/solana/new_pools?page=2',http.request.call_args.args[0])
        self.assertEqual(http.request.call_args.kwargs['headers']['Accept'],'application/json;version=20230203')

    def test_malformed_cross_network_future_and_missing_time_rejected(self):
        for mode in ('network','token','future','time','address'):
            row = pool()
            if mode=='network': row['id'] = 'eth_'+key(1)
            if mode=='token': row['relationships']['base_token']['data']['id'] = 'bsc_'+key(101)
            if mode=='future': row=pool(age=-1)
            if mode=='time': row['attributes']['pool_created_at'] = 'invalid'
            if mode=='address': row['attributes']['address'] = 'invalid'
            with self.subTest(mode=mode),self.assertRaises(ValueError): parse_pool(row,NOW)

    def test_unknown_market_cap_is_not_replaced_with_fdv(self):
        row=pool(cap=None)
        row['attributes']['fdv_usd']='999999'
        self.assertIsNone(parse_pool(row,NOW)['market_cap'])

    def test_bad_response_cannot_look_like_empty_success(self):
        http=Mock()
        http.request.return_value=(dict(error='rate limit'),NOW)
        with self.assertRaises(ValueError): GeckoTerminal(Mock(),http).new_pools()

    def test_durable_warmup_rotation_expiry_and_turnover(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'queue.sqlite'
            rules=replace(Rules(),min_age_hours=.25,max_age_hours=72)
            desk=Desk(path,rules)
            young=parse_pool(pool(1,age=60),NOW)
            first=parse_pool(pool(2,volume='200000'),NOW)
            second=parse_pool(pool(3,volume='100000'),NOW)
            selected,stats=queue(desk,[young,second,first],1,NOW)
            self.assertEqual(selected[0]['token'],first['token'])
            self.assertEqual(stats['retained_tokens'],3)
            self.assertEqual(stats['age_eligible_tokens'],2)
            desk.close()
            desk=Desk(path,rules)
            selected,_=queue(desk,[],1,NOW+10)
            self.assertEqual(selected[0]['token'],second['token'])
            # A mint can mature after it has disappeared from the fresh feed.
            selected,_=queue(desk,[],1,NOW+1000)
            self.assertEqual(selected[0]['token'],young['token'])
            selected,stats=queue(desk,[],5,NOW+73*3600)
            self.assertEqual(selected,[])
            self.assertEqual(stats['retained_tokens'],0)
            desk.close()

    def test_duplicate_mint_new_pool_does_not_reset_age(self):
        with tempfile.TemporaryDirectory() as folder:
            desk=Desk(Path(folder)/'queue.sqlite')
            first=parse_pool(pool(1,age=8*3600),NOW)
            queue(desk,[first],1,NOW)
            newer=parse_pool(pool(2,age=60),NOW)
            newer['token']=first['token']
            selected,_=queue(desk,[newer],1,NOW+1)
            self.assertEqual(selected[0]['pool'],first['pool'])
            self.assertEqual(selected[0]['created_at'],first['created_at'])
            selected,_=queue(desk,[],1,NOW+2,excluded=[first['token']])
            self.assertEqual(selected,[])
            desk.close()

    def test_ai_cycle_uses_new_pool_queue_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            desk=Desk(Path(folder)/'queue.sqlite',replace(Rules(),require_preflight=True,min_age_hours=.25,max_age_hours=72))
            args=Namespace(command='ai-scan',ai_config=None,limit=1)
            candidate=parse_pool(pool(),NOW)
            snapshot=dict(chain='solana',token=candidate['token'],pair=candidate['pool'],symbol='TEST',
                          observed_at=NOW,created_at=NOW-3600,price=1,liquidity=1,volume_24h=1,volume_6h=1)
            with patch('desk.ai_cli.GeckoTerminal') as gecko,patch('desk.ai_cli.DexScreener') as dex, \
                 patch('desk.ai_cli.AIProviders'),patch('desk.ai_cli.evaluate') as evaluate, \
                 patch('desk.cli.monitor'),patch('desk.ai_cli.time.time',return_value=NOW):
                gecko.return_value.new_pools.return_value=[candidate]
                dex.return_value.pairs.return_value=[snapshot]
                evaluate.return_value=dict(state='complete',paper_buys=0)
                result=run(args,desk)
                self.assertFalse(dex.return_value.discover.called)
                dex.return_value.pairs.assert_called_once_with(candidate['token'])
                recorded=evaluate.call_args.args[2][0]
                self.assertEqual(recorded['discovery']['source'],'geckoterminal_solana_new_pools')
                self.assertEqual(result['discovery']['scheduled_tokens'],1)
                self.assertFalse(result['discovery']['fetch_failed'])
            desk.close()
