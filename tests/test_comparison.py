import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from desk.core import Desk, Rules
from desk.comparison import search, rank, report, schedule
from test_preflight import key
from test_desk import NOW

class ComparisonTests(unittest.TestCase):
    def test_search_rejects_wrong_chain_and_deduplicates(self):
        p=dict(chainId='solana',baseToken=dict(address=key(1)),pairAddress=key(2),pairCreatedAt=(NOW-3600)*1000,liquidity=dict(usd=50000))
        scanner=Mock();scanner.get.return_value=(dict(pairs=[p,p,dict(p,chainId='ethereum')]),NOW)
        self.assertEqual(len(search(scanner,['pump fun'])),1)
        self.assertIn('pump%20fun',scanner.get.call_args.args[0])
        with self.assertRaises(ValueError):search(scanner,[''] )

    def test_queue_rotates_and_retains_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'a.sqlite',Rules(min_age_hours=.25,max_age_hours=72))
            found=[dict(token=key(i),created_at=NOW-3600) for i in (1,2)]
            a,_=schedule(desk,found,1,NOW)
            b,_=schedule(desk,[],1,NOW+300)
            self.assertNotEqual(a[0]['token'],b[0]['token'])

    def test_preflight_after_market_snapshot_is_not_future_evidence(self):
        from dataclasses import replace
        from test_desk import sample
        from test_preflight import evidence, TOKEN
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'a.sqlite',replace(Rules(),require_preflight=True,ticket=5,max_top10_owner_pct=60))
            e=evidence()
            # Market was sampled first, followed by RPC and sequential quote calls.
            s=sample(token=TOKEN,now=NOW-10,holders=e['holders'],preflight=e)
            with patch('desk.comparison.time.time',return_value=NOW+2):
                ranked=rank(desk,1,[s])[0]
            self.assertNotIn('buy_quote_stale',ranked['scan_ranking']['blocking_reasons'])
            self.assertNotIn('sell_quote_stale',ranked['scan_ranking']['blocking_reasons'])
            self.assertNotIn('early_rejection_reasons',ranked)
            with patch('desk.comparison.time.time',return_value=NOW+100):
                stale=rank(desk,2,[dict(s)])[0]
            self.assertIn('buy_quote_stale',stale['scan_ranking']['blocking_reasons'])
            self.assertIn('sell_quote_stale',stale['scan_ranking']['blocking_reasons'])
            desk.close()

    def test_holders_growth_and_two_window_sell_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'a.sqlite',Rules())
            def snapshot(t,n,price=1):
                return dict(token=key(1),pair=key(2),observed_at=t,price=price,buys_1h=2,sells_1h=3,
                    holders=dict(token=key(1),method='owner_aggregated',source='rpc',owner_count=n),discovery=dict(streams=['dexscreener_search']))
            rank(desk,1,[snapshot(NOW,100)])
            current=rank(desk,2,[snapshot(NOW+300,120,1.1)])[0]
            self.assertAlmostEqual(current['scan_ranking']['holder_growth_pct'],20)
            self.assertIn('scan_sells_dominate_two_observations',current['scan_ranking']['blocking_reasons'])
            self.assertEqual(report(desk)['streams']['dexscreener_search']['observed'],1)
            changed=snapshot(NOW+600,200);changed['pair']=key(3)
            self.assertIsNone(rank(desk,3,[changed])[0]['scan_ranking']['holder_growth_pct'])
