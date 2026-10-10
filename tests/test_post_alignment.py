import copy
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock,patch
from desk.core import Rules,Desk,vet
from desk.onchain import mint_evidence,owner_evidence,largest_account_evidence,PostSolanaRPC,TOKEN_2022_PROGRAM
from desk.scanner import DexScreener
from desk.discovery import queue,GeckoTerminal
from desk.preflight import preflight_reasons
from test_preflight import TOKEN,key,mint_result,accounts_result,evidence
from test_desk import NOW,sample


def fee_mint():
    r=mint_result();r['value']['owner']=TOKEN_2022_PROGRAM
    r['value']['data']['parsed']['info']['extensions']=[dict(extension='transferFeeConfig',state=dict(
        transferFeeConfigAuthority=key(90),withdrawWithheldAuthority=None,withheldAmount='100',
        olderTransferFee=dict(epoch=1,maximumFee='1000',transferFeeBasisPoints=100),
        newerTransferFee=dict(epoch=2,maximumFee='1000',transferFeeBasisPoints=200)))]
    return r

class PostAlignmentTests(unittest.TestCase):
    def test_transfer_fees_require_opt_in_and_valid_schedules(self):
        with self.assertRaises(ValueError):mint_evidence(TOKEN,fee_mint(),NOW)
        m=mint_evidence(TOKEN,fee_mint(),NOW,True)
        self.assertEqual(m['transfer_fee']['newerTransferFee']['transferFeeBasisPoints'],200)
        for field in ('authority','bps','epoch','missing'):
            r=fee_mint();s=r['value']['data']['parsed']['info']['extensions'][0]['state']
            if field=='authority':s['transferFeeConfigAuthority']='invalid'
            if field=='bps':s['newerTransferFee']['transferFeeBasisPoints']=10001
            if field=='epoch':s['olderTransferFee']['epoch']=3
            if field=='missing':del s['newerTransferFee']['maximumFee']
            with self.assertRaises((ValueError,KeyError)):mint_evidence(TOKEN,r,NOW,True)

    def test_withheld_supply_is_reconciled_without_counting_as_spendable(self):
        m=mint_evidence(TOKEN,fee_mint(),NOW,True);r=accounts_result()
        for row in r['value']:
            row['account']['owner']=TOKEN_2022_PROGRAM
            i=row['account']['data']['parsed']['info'];i['extensions']=[dict(extension='transferFeeAmount',state=dict(withheldAmount='5'))]
            i['tokenAmount']['amount']='490'
        h=owner_evidence(TOKEN,m,r,NOW)
        self.assertEqual(h['accounted_supply'],'10000');self.assertEqual(h['withheld_supply'],'200')
        self.assertEqual(h['top_account_pct'],4.9)
        r['value'].pop()
        with self.assertRaises(ValueError):owner_evidence(TOKEN,m,r,NOW)

    def test_post_account_measure_differs_from_owner_aggregation(self):
        m=mint_evidence(TOKEN,mint_result(),NOW);r=accounts_result()
        for row in r['value']:row['account']['data']['parsed']['info']['owner']=key(99)
        h=owner_evidence(TOKEN,m,r,NOW)
        self.assertEqual(h['top_owner_pct'],100);self.assertEqual(h['top_account_pct'],5)
        e=evidence();e['holders']=h;s=sample(token=TOKEN,holders=h,preflight=e)
        self.assertIn('owner_concentration',vet(s,Rules(),NOW))
        # Same numerical threshold, explicit account-based measurement.
        h['method']='token_accounts';h['supply_verified']=True
        rules=replace(Rules(),concentration_measure='token_accounts',min_holders=80,max_top10_owner_pct=60,ticket=5,require_preflight=True)
        h['owner_count']=None
        self.assertEqual(vet(s,rules,NOW),[])

    def test_largest_accounts_fail_closed_without_full_enumeration(self):
        m=mint_evidence(TOKEN,mint_result(),NOW)
        r=dict(context=dict(slot=101),value=[dict(address=key(i+1),amount='500',decimals=6) for i in range(20)])
        h=largest_account_evidence(TOKEN,m,r,NOW)
        self.assertEqual(h['top_account_pct'],5);self.assertIsNone(h['owner_count'])
        for field in ('duplicate','supply','old'):
            bad=copy.deepcopy(r)
            if field=='duplicate':bad['value'][1]=bad['value'][0]
            if field=='supply':bad['value'][0]['amount']='10001'
            if field=='old':bad['context']['slot']=99
            with self.assertRaises(ValueError):largest_account_evidence(TOKEN,m,bad,NOW)

    def test_post_rpc_uses_read_only_largest_account_and_supply_methods(self):
        http=Mock();recorder=Mock()
        largest=dict(context=dict(slot=102),value=[dict(address=key(1),amount='500',decimals=6)])
        supply=dict(context=dict(slot=101),value=dict(amount='10000',decimals=6))
        http.request.side_effect=[({'id':1,'result':mint_result()},NOW),({'id':1,'result':supply},NOW),({'id':1,'result':largest},NOW)]
        m,h=PostSolanaRPC(recorder,http).inspect(TOKEN)
        methods=[call.kwargs['payload']['method'] for call in http.request.call_args_list]
        self.assertEqual(methods,['getAccountInfo','getTokenSupply','getTokenLargestAccounts'])
        self.assertEqual(h['method'],'token_accounts')
        self.assertTrue(h['supply_verified'])
        self.assertEqual(h['top_accounts'][0]['account'],key(1))
        with self.assertRaises(ValueError):PostSolanaRPC(recorder,http).call('sendTransaction',[])

    def test_post_mode_still_blocks_low_indexed_holders(self):
        from desk.filters import additional_checks
        rules=replace(Rules(),concentration_measure='token_accounts',min_holders=80)
        missing=next(c for c in additional_checks(dict(holders={}),rules) if c['check']=='holders')
        self.assertEqual(missing['status'],'skipped')
        low=next(c for c in additional_checks(dict(holders=dict(owner_count=79)),rules) if c['check']=='holders')
        self.assertEqual(low['status'],'fail')

    def test_batch_markets_bound_30_and_bind_mints(self):
        scanner=DexScreener(Mock());scanner.get=Mock(return_value=([],NOW))
        tokens=[key(i+1) for i in range(61)]
        found=scanner.batch_pairs(tokens)
        self.assertEqual(len(found),61);self.assertEqual(scanner.get.call_count,3)
        self.assertEqual(len(scanner.get.call_args_list[0].args[0].split('/')[-1].split(',')),30)

    def test_market_priority_does_not_spend_slot_on_known_weak_pool(self):
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'d.sqlite',replace(Rules(),min_age_hours=.25,min_market_cap=60000))
            weak=dict(token=key(1),pool=key(11),created_at=NOW-3600,liquidity=1,volume_24h=1,market_cap=1)
            strong=dict(weak,token=key(2),pool=key(12),liquidity=100000,volume_24h=100000,market_cap=100000)
            chosen,_=queue(desk,[weak,strong],1,NOW,prioritize_market=True)
            self.assertEqual(chosen[0]['token'],key(2));desk.close()

    def test_indexed_holder_count_identity_and_missing(self):
        http=Mock();gt=GeckoTerminal(Mock(),http)
        http.request.return_value=(dict(data=dict(id='solana_'+TOKEN,attributes=dict(holders=dict(count=123)))),NOW)
        self.assertEqual(gt.token_info(TOKEN)['holder_count'],123)
        http.request.return_value=(dict(data=dict(id='solana_'+key(1),attributes={})),NOW)
        with self.assertRaises(ValueError):gt.token_info(TOKEN)
