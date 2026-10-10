import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from desk.bench import minutes
from desk.core import Desk, Rules, volume_ratio
from desk.onchain import TOKEN_PROGRAM, exclude_pool_vaults, largest_account_evidence, mint_evidence
from desk.post_policy import fixed_size, size
from desk.reporting import Telegram, after_cycle
from desk import whale
from test_desk import NOW, sample
from test_preflight import TOKEN, key, mint_result
from test_post_evaluate import Providers


def owner_rows(owners):
    return dict(value=[dict(owner=TOKEN_PROGRAM, executable=False, data=dict(parsed=dict(type='account',
        info=dict(mint=TOKEN, owner=owner, state='initialized', tokenAmount=dict(amount='1', decimals=6))))) for owner in owners])


class VaultExclusion(unittest.TestCase):
    def test_traded_pool_and_amm_authority_vaults_are_excluded_but_reported(self):
        mint = mint_evidence(TOKEN, mint_result(), NOW)
        largest = dict(context=dict(slot=101), value=[dict(address=key(1), amount='1300', decimals=6),
            dict(address=key(2), amount='800', decimals=6), dict(address=key(3), amount='300', decimals=6)])
        holders = largest_account_evidence(TOKEN, mint, largest, NOW)
        self.assertEqual(holders['top_account_pct'], 13)
        pool = key(50)
        result = exclude_pool_vaults(holders, mint, owner_rows([pool, '5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1', key(60)]),
                                     pool, {'5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1': 'raydium_amm_v4_authority'})
        self.assertEqual(result['raw_top_account_pct'], 13)
        self.assertEqual(result['top_account_pct'], 3)
        self.assertEqual([e['reason'] for e in result['exclusions']], ['traded_pool', 'raydium_amm_v4_authority'])

    def test_unreadable_owner_stays_in_the_measurement(self):
        mint = mint_evidence(TOKEN, mint_result(), NOW)
        holders = largest_account_evidence(TOKEN, mint, dict(context=dict(slot=101), value=[dict(address=key(1), amount='1300', decimals=6)]), NOW)
        result = exclude_pool_vaults(holders, mint, dict(value=[None]), key(50), {})
        self.assertEqual(result['top_account_pct'], 13)
        self.assertEqual(result['exclusions'], [])


class Sizing(unittest.TestCase):
    def test_liquidity_fraction_caps_ticket(self):
        self.assertEqual(size(10000, 10000, .8, 1, 100000, liquidity_fraction=.005)['dollars'], 500)

    def test_fixed_fraction_is_labelled_and_fee_checked(self):
        result = fixed_size(10000, 10000, .015, 1000000, partial=True, liquidity_fraction=.005)
        self.assertEqual(result['dollars'], 90)
        self.assertEqual(result['reason'], 'paper_fixed_fraction_assumption')
        self.assertEqual(fixed_size(1000, 1000, .015, 1000000)['dollars'], 0)
        with self.assertRaises(ValueError):
            fixed_size(10000, 10000, .07, 1000000)


class Bench(unittest.TestCase):
    def test_reason_specific_minutes(self):
        self.assertEqual(minutes(['owner_concentration', 'top_wallet_concentration']), 100000)
        self.assertEqual(minutes(['owner_concentration', 'top10_concentration']), 90)
        self.assertEqual(minutes(['invalid_owner_concentration', 'onchain_evidence_unavailable']), 45)
        self.assertEqual(minutes(['liquidity', 'volume']), 25)
        self.assertEqual(minutes(['pair_age_out_of_range']), 20)
        self.assertEqual(minutes(['effort']), 45)


class Exits(unittest.TestCase):
    def test_volume_ratio_is_age_normalized(self):
        # Three hours old: the post's h6/(h24/4) reads 4.0 here and can never exit.
        young = sample(created_at=NOW - 3 * 3600, volume_6h=30000, volume_24h=30000, volume_windows={'h1': 500})
        self.assertAlmostEqual(volume_ratio(young), 500 / (30000 / 3))
        # Twelve hours old: the true six-hour average is h24/2, not h24/4.
        half_day = sample(created_at=NOW - 12 * 3600, volume_6h=1500, volume_24h=10000)
        self.assertAlmostEqual(volume_ratio(half_day), .3)
        self.assertIsNone(volume_ratio(sample(created_at=NOW - 1800)))
        self.assertAlmostEqual(volume_ratio(sample(volume_6h=1000, volume_24h=40000)), .1)

    def desk(self, d, **rules):
        settings = dict(exit_policy='post_volume', minimum_fee_usd=.95, fee_bps=45, fixed_fee_usd=0, max_exposure=60)
        return Desk(Path(d) / 'paper.sqlite', Rules(**dict(settings, **rules)))

    def test_liquidity_collapse_closes_a_young_pair(self):
        with tempfile.TemporaryDirectory() as d:
            desk = self.desk(d, rug_liquidity_drop_pct=50)
            try:
                run = desk.run('test', NOW)
                desk.record(run, sample(), NOW)
                desk.record(run, sample(now=NOW + 60, liquidity=60000), NOW + 60, enter=False)
                self.assertEqual(len(desk.open_positions()), 1)
                desk.record(run, sample(now=NOW + 120, liquidity=40000), NOW + 120, enter=False)
                self.assertEqual(len(desk.open_positions()), 0)
                self.assertIn('liquidity_collapse', desk.db.execute("SELECT reasons FROM decisions WHERE action='sell'").fetchone()[0])
            finally:
                desk.close()

    def test_whale_hold_is_bounded_and_respected_without_chain_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            desk = self.desk(d, whale_hold_max_seconds=600)
            try:
                run = desk.run('test', NOW)
                desk.record(run, sample(), NOW)
                fading = dict(volume_6h=100, volume_24h=40000)
                desk.record(run, sample(now=NOW + 60, whale=dict(distributing=True, account='w', observed_at=NOW + 60), **fading), NOW + 60, enter=False)
                self.assertEqual(len(desk.open_positions()), 1)
                desk.record(run, sample(now=NOW + 120, **fading), NOW + 120, enter=False)
                self.assertEqual(len(desk.open_positions()), 1)
                desk.record(run, sample(now=NOW + 700, whale=dict(distributing=True, account='w', observed_at=NOW + 700), **fading), NOW + 700, enter=False)
                self.assertEqual(len(desk.open_positions()), 0)
            finally:
                desk.close()

    def test_one_retest_add_after_a_confirmed_fifty_percent_move(self):
        with tempfile.TemporaryDirectory() as d:
            # The add must clear the same 3% round-trip fee ceiling, so the ticket is $200.
            desk = self.desk(d, retest_add_fraction=.5, max_ticket_liquidity_pct=2, ticket=200, max_exposure=400)
            try:
                run = desk.run('test', NOW)
                desk.record(run, sample(), NOW)
                desk.record(run, sample(price=1.6, now=NOW + 60), NOW + 60, enter=False)
                desk.record(run, sample(price=1.25, now=NOW + 120), NOW + 120, enter=False)
                desk.record(run, sample(price=1.25, now=NOW + 180), NOW + 180, enter=False)
                buys = desk.db.execute("SELECT COUNT(*) FROM fills WHERE side='buy'").fetchone()[0]
                self.assertEqual(buys, 2)
                self.assertAlmostEqual(float(desk.open_positions()[0]['cost']), 200.95 + 100 + .95, places=6)
            finally:
                desk.close()


class Whale(unittest.TestCase):
    def test_distribution_needs_a_falling_balance_above_the_limit_and_skips_vaults(self):
        with tempfile.TemporaryDirectory() as d:
            desk = Desk(Path(d) / 'paper.sqlite', Rules(max_top_owner_pct=5))
            rpc = Mock()
            balances = [['900', '800'], ['700', '600']]
            def call(method, params):
                if method == 'getTokenSupply':
                    return dict(value=dict(amount='10000')), NOW
                amounts = balances.pop(0)
                return dict(value=[dict(address=key(1), amount=amounts[0]), dict(address=key(2), amount=amounts[1])]), NOW
            rpc.call.side_effect = call
            rpc.owners.return_value = {key(1): 'pool', key(2): key(70)}
            try:
                self.assertFalse(whale.check(desk, rpc, TOKEN, 'pool', NOW)['distributing'])
                result = whale.check(desk, rpc, TOKEN, 'pool', NOW + 300)
                self.assertTrue(result['distributing'])
                self.assertEqual(result['account'], key(2))
            finally:
                desk.close()


class Reporting(unittest.TestCase):
    def test_unconfigured_telegram_is_a_no_op(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(after_cycle(Mock(), {}), 'not_configured')

    def test_cycle_day_and_material_move_messages(self):
        with tempfile.TemporaryDirectory() as d:
            desk = Desk(Path(d) / 'paper.sqlite', Rules())
            from desk.ai import init
            init(desk)
            telegram = Mock(configured=True)
            try:
                result = dict(state='complete', discovery=dict(free_cut=dict(observed=9, passed=1)), shortlisted=0, calls=2)
                self.assertEqual(after_cycle(desk, result, telegram, now=NOW), 'sent')
                self.assertIn('9 seen', telegram.send.call_args.args[0])
                with desk.db:
                    desk.db.execute("UPDATE account SET cash='1200'")
                after_cycle(desk, result, telegram, now=NOW + 86400)
                texts = [c.args[0] for c in telegram.send.call_args_list[1:]]
                self.assertTrue(any('end of day' in t for t in texts))
                self.assertTrue(any('equity moved +20.0%' in t for t in texts))
            finally:
                desk.close()


class SocialDefer(unittest.TestCase):
    @patch('desk.post_evaluate.time.time', return_value=NOW)
    def test_published_handle_waits_for_grok_evidence_then_proceeds(self, clock):
        from desk.ai import AIPolicy
        from desk.post_evaluate import evaluate
        from test_preflight import evidence
        for requested, calls in ((NOW - 60, 0), (NOW - 1900, 2)):
            with self.subTest(requested=requested), tempfile.TemporaryDirectory() as d:
                desk = Desk(Path(d) / 'paper.sqlite', Rules(require_preflight=True, max_top10_owner_pct=60, exit_policy='post_volume'))
                try:
                    e = evidence()
                    s = sample(token=TOKEN, holders=e['holders'], preflight=e, observe_only=True,
                               indexed_dossier=dict(x_handle='project'), social_job=dict(requested_at=requested))
                    first = desk.run('history', NOW - 30)
                    desk.record(first, dict(s, observed_at=NOW - 30), NOW - 30, enter=False)
                    provider = Providers()
                    evaluate(desk, desk.run('post', NOW), [s], provider, AIPolicy(judgment_profile='post', max_calls=10))
                    self.assertEqual(provider.calls, calls)
                finally:
                    desk.close()

    @patch('desk.post_evaluate.time.time', return_value=NOW)
    def test_judge_failure_skips_candidate_without_benching(self, clock):
        from desk.ai import AIPolicy
        from desk.post_evaluate import evaluate
        from test_preflight import evidence
        with tempfile.TemporaryDirectory() as d:
            desk = Desk(Path(d) / 'paper.sqlite', Rules(require_preflight=True, max_top10_owner_pct=60, exit_policy='post_volume'))
            try:
                e = evidence()
                s = sample(token=TOKEN, holders=e['holders'], preflight=e)
                first = desk.run('history', NOW - 30)
                desk.record(first, dict(s, observed_at=NOW - 30), NOW - 30, enter=False)
                provider = Mock(calls=1)
                provider.judge.side_effect = RuntimeError('provider HTTP 502')
                result = evaluate(desk, desk.run('post', NOW), [s], provider, AIPolicy(judgment_profile='post', max_calls=10))
                self.assertEqual(result['state'], 'degraded')
                self.assertIsNone(desk.db.execute('SELECT 1 FROM post_bench WHERE token=?', (TOKEN,)).fetchone())
            finally:
                desk.close()


if __name__ == '__main__':
    unittest.main()


class SmallBankProfile(unittest.TestCase):
    def test_hundred_dollar_profile_sizes_full_evidence_and_blocks_fee_heavy_partials(self):
        import json
        from desk.supervisor import validate_entry_setup
        root = Path(__file__).resolve().parents[1]
        config = root / 'post-paper-100.config.json'
        r = Rules(**json.loads(config.read_text()))
        self.assertEqual(r.initial_cash, 100)
        full = fixed_size(100, 100, r.paper_fixed_fraction, 100000, liquidity_fraction=r.max_ticket_liquidity_pct / 100,
                          max_fee_pct=r.max_roundtrip_fee_pct, max_fraction=r.max_bank_fraction)
        self.assertEqual(full['dollars'], 40)
        partial = fixed_size(100, 100, r.paper_fixed_fraction, 100000, partial=True, liquidity_fraction=r.max_ticket_liquidity_pct / 100,
                             max_fee_pct=r.max_roundtrip_fee_pct, max_fraction=r.max_bank_fraction)
        self.assertEqual(partial['dollars'], 0)
        with tempfile.TemporaryDirectory() as d:
            validate_entry_setup(root, str(Path(d) / 'small.sqlite'), None, config)
