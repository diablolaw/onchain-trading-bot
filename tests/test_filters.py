import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from desk.core import Desk, Rules, normalize, vet
from desk.filters import additional_checks, report, save, rejection_report, SCHEMA
from test_desk import NOW,sample
from test_preflight import TOKEN,evidence


def aligned():
    return replace(Rules(),require_preflight=True,ticket=5,min_age_hours=.25,max_age_hours=72,
        min_liquidity=12000,min_volume_24h=40000,min_market_cap=60000,max_market_cap=8000000,
        min_trades_24h=150,min_holders=80,reject_no_sells=True,max_top_owner_pct=5,max_top10_owner_pct=60)


def snapshot():
    s=sample(token=TOKEN,market_cap=60000,buys_24h=100,sells_24h=50,buys_1h=20,sells_1h=0,
             preflight=evidence(),holders=evidence()['holders'],liquidity=12000,volume_24h=40000)
    s['holders']['owner_count']=80
    return s


class FilterTests(unittest.TestCase):
    def test_post_boundaries_and_inclusive_limits(self):
        r,s=aligned(),snapshot()
        self.assertEqual(vet(s,r,NOW),[])
        s['market_cap']=8000000
        s['holders']['top_owner_pct']=5
        s['holders']['top10_owner_pct']=60
        self.assertEqual(vet(s,r,NOW),[])
        for field,value,reason in [('market_cap',59999,'market_cap_out_of_range'),
                                   ('market_cap',8000001,'market_cap_out_of_range'),
                                   ('buys_24h',99,'too_few_trades'),('buys_1h',21,'buys_without_sells')]:
            bad=copy.deepcopy(s);bad[field]=value
            self.assertIn(reason,vet(bad,r,NOW))
        for field,value in [('top_owner_pct',5.01),('top10_owner_pct',60.01)]:
            bad=copy.deepcopy(s);bad['holders'][field]=value
            self.assertIn('owner_concentration',vet(bad,r,NOW))
        s['holders']['owner_count']=79
        self.assertIn('too_few_holders',vet(s,r,NOW))

    def test_missing_is_distinct_from_threshold_failure(self):
        s=snapshot();s['market_cap']=None;s['buys_24h']=None
        rows={c['check']:c for c in additional_checks(s,aligned())}
        self.assertEqual(rows['market_cap']['status'],'missing')
        self.assertEqual(rows['trades_24h']['status'],'missing')
        s['market_cap']=1
        rows={c['check']:c for c in additional_checks(s,aligned())}
        self.assertEqual(rows['market_cap']['status'],'fail')

    def test_no_sells_rule_and_missing_counts(self):
        s=snapshot();s['buys_1h']=21;s['sells_1h']=1
        self.assertNotIn('buys_without_sells',vet(s,aligned(),NOW))
        s['sells_1h']=None
        self.assertIn('sell_activity_missing',vet(s,aligned(),NOW))
        s['sells_1h']=.5
        self.assertIn('sell_activity_missing',vet(s,aligned(),NOW))

    def test_legacy_rules_do_not_enable_new_filters(self):
        self.assertEqual(vet(sample(),Rules(),NOW),[])

    def test_no_fdv_substitution_and_both_trade_counts_required(self):
        pair=dict(chainId='solana',baseToken=dict(address=TOKEN),pairAddress=TOKEN,fdv=900000,
                  txns=dict(h24=dict(buys=150)))
        s=normalize(pair,NOW)
        self.assertIsNone(s['market_cap'])
        rows={c['check']:c for c in additional_checks(s,aligned())}
        self.assertEqual(rows['trades_24h']['status'],'missing')

    def test_early_reject_reports_preflight_as_skipped(self):
        s=snapshot();s['liquidity']=100;s['holders']=None;s.pop('preflight')
        s['early_rejection_reasons']=['low_or_missing_liquidity']
        rows={c['check']:c for c in report(s,aligned(),NOW)['checks']}
        self.assertEqual(rows['liquidity_usd']['status'],'fail')
        self.assertEqual(rows['liquidity_usd']['value'],100)
        self.assertEqual(rows['holders']['status'],'skipped')
        self.assertEqual(rows['mint_authority']['status'],'skipped')
        self.assertEqual(rows['roundtrip_quotes']['status'],'skipped')

    def test_missing_preflight_does_not_report_active_authorities(self):
        s=snapshot();s.pop('preflight');s['holders']=None
        rows={c['check']:c for c in report(s,aligned(),NOW)['checks']}
        self.assertEqual(rows['mint_authority']['status'],'missing')
        self.assertIsNone(rows['mint_authority']['value'])
        self.assertEqual(rows['roundtrip_quotes']['status'],'missing')

    def test_execution_checks_preserved_and_separated(self):
        s=snapshot();s['preflight']['quotes']['buy']['price_impact_pct']=2
        r=report(s,aligned(),NOW)
        row=next(c for c in r['checks'] if c['check']=='buy_price_impact_pct')
        self.assertEqual(row['group'],'execution_safety')
        self.assertEqual(row['status'],'fail')
        self.assertIn('buy_price_impact_limit',vet(s,aligned(),NOW))

    def test_persistent_report_uses_original_rules_and_latest_run(self):
        with tempfile.TemporaryDirectory() as folder:
            desk=Desk(Path(folder)/'paper.sqlite',aligned());desk.db.executescript(SCHEMA)
            run=desk.run('ai-paper',NOW);save(desk,run,snapshot(),NOW)
            newer=desk.run('ai-paper',NOW+1);s=snapshot();s['market_cap']=1;save(desk,newer,s,NOW+1)
            desk.rules=Rules()
            latest=rejection_report(desk)
            self.assertIn('run '+str(newer),latest)
            self.assertIn('FAIL [strategy] market_cap: 1',latest)
            self.assertIn('60000',latest)
            self.assertIn('PASS (AI/sizing may still reject)',rejection_report(desk,run))
            desk.close()

    def test_invalid_configuration_rejected(self):
        for kw in (dict(min_market_cap=100,max_market_cap=99),dict(min_trades_24h=1.5),dict(min_holders=True),dict(reject_no_sells=1)):
            with self.assertRaises(ValueError): Rules(**kw)
