import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from desk.core import Desk,Rules,encode
from desk.discovery_feed import poll,candidates
from test_preflight import TOKEN,key

class FeedTests(unittest.TestCase):
    def test_missing_age_in_refreshed_pair_does_not_crash_other_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'desk.sqlite',Rules(min_age_hours=.25,max_age_hours=72))
            try:
                from desk.discovery_feed import init
                init(desk)
                for i,created in enumerate((None, 'bad', -1, 3000, 900)):
                    token=key(i+1)
                    data=dict(token=token,pair=key(20),created_at=created,liquidity=20000,volume_24h=50000,market_cap=100000)
                    desk.db.execute('INSERT INTO launch_feed VALUES (?,?,?)',(token,2000,encode(data)))
                rows,status=candidates(desk,now=2000)
                self.assertEqual([r['token'] for r in rows],[key(5)])
                self.assertEqual(status['invalid_age_tokens'],4)
            finally:desk.close()

    def test_feed_is_fresh_bounded_and_rejects_wrong_chain(self):
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'desk.sqlite',Rules(min_age_hours=.25,max_age_hours=72))
            scanner=Mock()
            pair=dict(chainId='solana',pairAddress=key(1),baseToken=dict(address=TOKEN),pairCreatedAt=900000,liquidity=dict(usd=20000),volume=dict(h24=50000),priceUsd='1',marketCap=100000)
            scanner.get.return_value=({'pairs':[pair,dict(pair,chainId='base')]},2000)
            try:
                status=poll(desk,['pump'],scanner,now=2000)
                self.assertEqual(status['observed'],1)
                rows,state=candidates(desk,now=2000)
                self.assertEqual(rows[0]['token'],TOKEN)
                self.assertFalse(state['fetch_failed'])
                self.assertEqual(candidates(desk,now=2121)[0],[])
                self.assertEqual(candidates(desk,excluded={TOKEN},now=2000)[0],[])
                scanner.get.side_effect=RuntimeError('provider failed')
                self.assertTrue(poll(desk,['pump'],scanner,now=2001)['degraded'])
                self.assertTrue(candidates(desk,now=2001)[1]['fetch_failed'])
            finally:desk.db.close()
