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

    def test_first_cycle_waits_for_scan_instead_of_reporting_a_failed_fetch(self):
        from desk.discovery_feed import init,wait_for_first_poll
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'desk.sqlite',Rules(min_age_hours=.25,max_age_hours=72))
            try:
                init(desk);ticks=[0]
                def sleep(seconds):
                    ticks[0]+=seconds
                    if ticks[0]==4:desk.db.execute('INSERT INTO launch_feed_status VALUES (1,?,?)',(2000,encode(dict(degraded=False))))
                self.assertTrue(wait_for_first_poll(desk,sleep=sleep,clock=lambda:ticks[0],now=lambda:2010))
                self.assertEqual(ticks[0],4)
                self.assertFalse(candidates(desk,now=2010)[1]['fetch_failed'])
                # After a restart the previous run's status is still there; it must not end the wait.
                self.assertFalse(wait_for_first_poll(desk,timeout=6,sleep=sleep,clock=lambda:ticks[0],now=lambda:2900))
                desk.db.execute('DELETE FROM launch_feed_status')
                self.assertFalse(wait_for_first_poll(desk,timeout=6,sleep=sleep,clock=lambda:ticks[0],now=lambda:2010))
            finally:desk.close()

    def test_full_watch_list_keeps_active_older_mints_over_newer_dead_ones(self):
        from desk.discovery_feed import init
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'desk.sqlite',Rules(min_age_hours=.25,max_age_hours=72))
            try:
                init(desk);scanner=Mock();scanner.get.return_value=({'pairs':[]},10**6);scanner.batch_pairs.return_value={}
                with desk.db:
                    for i in range(501):
                        token=key(100+i);volume=90000 if i==0 else 10
                        desk.db.execute('INSERT INTO launch_watch VALUES (?,?,1)',(token,10**6-3600*20 if i==0 else 10**6-60))
                        desk.db.execute('INSERT INTO launch_feed VALUES (?,?,?)',(token,10**6,encode(dict(token=token,volume_24h=volume))))
                poll(desk,['pump'],scanner,now=10**6)
                tokens={r[0] for r in desk.db.execute('SELECT token FROM launch_watch')}
                self.assertEqual(len(tokens),500)
                self.assertIn(key(100),tokens)
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
