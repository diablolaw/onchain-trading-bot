import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from desk.core import Desk, Rules, normalize, vet
from desk.scanner import DexScreener, best_pair

NOW = 1800000000


def sample(price=1, token='mint', pair='pool', now=NOW, **updates):
    s = dict(chain='solana', token=token, pair=pair, symbol='TEST', observed_at=now,
             price=price, liquidity=100000, volume_6h=10000, volume_24h=40000,
             created_at=NOW-86400,
             holders=dict(token=token, method='owner_aggregated', source='test fixture',
                          observed_at=now, top_owner_pct=5, top10_owner_pct=25))
    s.update(updates)
    return s


class VetTests(unittest.TestCase):
    def test_determinism_and_fail_closed(self):
        s = sample()
        self.assertEqual(vet(s, Rules(), NOW), [])
        self.assertEqual(vet(s, Rules(), NOW), vet(s, Rules(), NOW))
        self.assertIn('missing_or_stale_owner_evidence', vet(sample(holders=None), Rules(), NOW))
        self.assertIn('stale_market_data', vet(s, Rules(), NOW+121))
        self.assertIn('stale_market_data', vet(s, Rules(), NOW-1))

    def test_concentration_and_age(self):
        s = sample()
        s['holders']['top_owner_pct'] = 11
        self.assertIn('owner_concentration', vet(s, Rules(), NOW))
        self.assertIn('pair_age_out_of_range', vet(sample(created_at=NOW+10), Rules(), NOW))
        s['holders']['method'] = 'largest_token_accounts'
        self.assertIn('missing_or_stale_owner_evidence', vet(s, Rules(), NOW))

    def test_invalid_numbers(self):
        for price in (None, float('nan'), float('inf'), -1, 0):
            self.assertIn('missing_price', vet(sample(price=price), Rules(), NOW))
        with self.assertRaises(ValueError):
            Rules(slippage_bps=10000)
        with self.assertRaises(ValueError):
            Rules(max_positions=1.2)

    def test_normalization(self):
        s = normalize({'chainId':'solana', 'baseToken':{'address':'mint'},
                       'pairAddress':'pool','priceUsd':'NaN','liquidity':None,
                       'pairCreatedAt':(NOW-86400)*1000}, NOW)
        self.assertIsNone(s['price'])
        self.assertIsNone(s['liquidity'])
        self.assertEqual(s['created_at'], NOW-86400)


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'desk.sqlite'
        self.desk = Desk(self.path)
        self.run = self.desk.run('test', NOW)

    def tearDown(self):
        self.desk.close()
        self.tmp.cleanup()

    def record(self, s, enter=True, now=None):
        return self.desk.record(self.run, s, s['observed_at'] if now is None else now, enter)

    def test_roundtrip_costs_and_restart(self):
        self.record(sample())
        self.assertEqual(len(self.desk.open_positions()), 1)
        self.assertEqual(Decimal(self.desk.report(NOW)['cash_usd']), Decimal('974.875'))
        self.desk.close()
        self.desk = Desk(self.path)
        self.record(sample(price=1.5, now=NOW+15), enter=False)
        report = self.desk.report(NOW+15)
        self.assertEqual(report['fills'], 2)
        self.assertEqual(report['positions'], [])
        self.assertEqual(Decimal(report['cash_usd']) - 1000, Decimal(report['realized_pnl_usd']))
        p = self.desk.db.execute('SELECT * FROM positions').fetchone()
        expected = Decimal(25)/Decimal('1.01')*Decimal('1.485')*Decimal('.997')-Decimal('.05')-Decimal('25.125')
        self.assertAlmostEqual(Decimal(p['pnl']), expected, places=7)

    def test_duplicate_ordering_and_cooldown(self):
        self.record(sample())
        self.record(sample(price=2))
        self.record(sample(price=2, now=NOW-1))
        self.assertEqual(self.desk.report(NOW)['fills'], 1)
        self.record(sample(price=2, now=NOW+15))
        self.assertEqual(self.desk.report(NOW+15)['fills'], 2)
        self.assertFalse(self.desk.open_positions())
        self.record(sample(price=2, now=NOW+20))
        self.assertEqual(self.desk.report(NOW+20)['fills'], 2)

    def test_exit_precedence_and_missing_evidence(self):
        self.record(sample())
        self.record(sample(price=.5, now=NOW+15, volume_6h=0, holders=None), enter=False)
        row = self.desk.db.execute("SELECT reasons FROM decisions WHERE action='sell'").fetchone()
        self.assertEqual(json.loads(row[0]), ['stop_loss'])

    def test_stale_exit_does_not_invent_fill(self):
        self.record(sample())
        self.record(sample(price=.5, now=NOW+1), enter=False, now=NOW+200)
        self.assertEqual(self.desk.report(NOW+200)['fills'], 1)
        self.assertTrue(self.desk.report(NOW+200)['positions'][0]['stale'])

    def test_pool_pinning(self):
        self.record(sample())
        self.record(sample(price=.1, pair='other', now=NOW+15), enter=False)
        self.assertEqual(self.desk.report(NOW+15)['fills'], 1)

    def test_exposure_and_cash_limits(self):
        self.desk.rules = replace(Rules(), max_exposure=30)
        self.record(sample())
        self.record(sample(token='second', pair='second_pool'))
        self.assertEqual(len(self.desk.open_positions()), 1)
        self.desk.rules = replace(Rules(), ticket=2000, max_exposure=3000)
        self.record(sample(token='third', pair='third_pool'))
        self.assertEqual(len(self.desk.open_positions()), 1)

    def test_atomic_rollback(self):
        self.desk._fill = Mock(side_effect=RuntimeError('simulated write failure'))
        with self.assertRaises(RuntimeError):
            self.record(sample())
        self.assertEqual(self.desk.report(NOW)['snapshots'], 0)
        self.assertFalse(self.desk.open_positions())
        self.assertEqual(Decimal(self.desk.report(NOW)['cash_usd']), 1000)

    def test_two_workers_cannot_double_fill(self):
        def worker(_):
            desk = Desk(self.path)
            try:
                run = desk.run('concurrent', NOW)
                desk.record(run, sample(), NOW)
            finally:
                desk.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(worker, range(2)))
        self.assertEqual(self.desk.report(NOW)['fills'], 1)
        self.assertEqual(len(self.desk.open_positions()), 1)
        self.assertEqual(Decimal(self.desk.report(NOW)['cash_usd']), Decimal('974.875'))

    def test_volume_exit(self):
        self.record(sample())
        self.record(sample(now=NOW+15, volume_6h=100), enter=False)
        self.assertEqual(self.desk.report(NOW+15)['fills'], 2)

    def test_time_and_liquidity_exits(self):
        self.record(sample())
        self.record(sample(now=NOW+86400), enter=False)
        self.assertFalse(self.desk.open_positions())
        self.record(sample(token='second', pair='second_pool', now=NOW+86401))
        self.record(sample(token='second', pair='second_pool', now=NOW+86402, liquidity=0), enter=False)
        self.assertFalse(self.desk.open_positions())


class ScannerTests(unittest.TestCase):
    def test_profile_filter_and_pair_selection(self):
        api = DexScreener(Mock())
        mint = 'A'*32
        api.get = Mock(return_value=([{'chainId':'solana','tokenAddress':mint},
                                      {'chainId':'ethereum','tokenAddress':'bad'},
                                      {'chainId':'solana','tokenAddress':'bad'}], NOW))
        self.assertEqual(api.discover(), [mint])
        self.assertEqual(best_pair([sample(pair='b'), sample(pair='a')])['pair'], 'a')
        self.assertIsNone(best_pair([]))

    def test_pair_identity(self):
        api = DexScreener(Mock())
        mint, pair = 'A'*32, 'B'*32
        api.get = Mock(return_value=({'pairs':[{'chainId':'solana','pairAddress':pair,
                                               'baseToken':{'address':'wrong'}}]}, NOW))
        with self.assertRaises(ValueError):
            api.pair(pair, mint)


if __name__ == '__main__':
    unittest.main()
