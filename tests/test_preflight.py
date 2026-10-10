import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from desk.core import Desk, Rules, vet
from desk.cli import fixture
from desk.http import JsonHTTP
from desk.onchain import SolanaRPC, TOKEN_PROGRAM, TOKEN_2022_PROGRAM, address, mint_evidence, owner_evidence
from desk.preflight import collect, preflight_reasons, quote_reasons
from desk.quotes import Jupiter, USDC, parse_quote
from test_desk import NOW, sample

TOKEN = 'So11111111111111111111111111111111111111112'


def key(index):
    # 32-byte keys with low integer values, encoded in base58.
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    digits = ''
    value = index
    while value:
        value, remainder = divmod(value, 58)
        digits = alphabet[remainder] + digits
    return '1'*31 + digits


def mint_result():
    return dict(context=dict(slot=100), value=dict(owner=TOKEN_PROGRAM, executable=False,
        data=dict(parsed=dict(type='mint', info=dict(supply='10000', decimals=6, isInitialized=True,
                                                   mintAuthority=None, freezeAuthority=None)))))


def accounts_result():
    rows = []
    for index in range(20):
        rows.append(dict(pubkey=key(index+1), account=dict(owner=TOKEN_PROGRAM, executable=False,
            data=dict(parsed=dict(type='account', info=dict(mint=TOKEN, owner=key(index+21), state='initialized',
                                                            tokenAmount=dict(amount='500', decimals=6)))))))
    return dict(context=dict(slot=101), value=rows)


def order(input_mint=USDC, output_mint=TOKEN, amount=5000000, output=25000000):
    return dict(inputMint=input_mint, outputMint=output_mint, inAmount=str(amount), outAmount=str(output),
                otherAmountThreshold=str(output*99//100), swapMode='ExactIn', slippageBps=100,
                priceImpact=-.2, feeBps=10, feeMint=USDC, routePlan=[{'swapInfo':{'label':'test'}}],
                router='metis', transaction=None, signatureFeeLamports=5000,
                prioritizationFeeLamports=1000, rentFeeLamports=0)


def quote_evidence():
    buy = parse_quote(order(), USDC, TOKEN, 5000000, NOW)
    sell = parse_quote(order(TOKEN, USDC, 24750000, 5000000), TOKEN, USDC, 24750000, NOW)
    return dict(token=TOKEN, quote_currency=USDC, ticket_usdc='5', buy=buy, sell=sell,
                worst_quote_roundtrip_loss_pct=1, network_lamports=12000)


def evidence():
    mint = mint_evidence(TOKEN, mint_result(), NOW)
    owners = owner_evidence(TOKEN, mint, accounts_result(), NOW)
    return dict(token=TOKEN, mint=mint, holders=owners, quotes=quote_evidence(), errors=[])


class OnchainTests(unittest.TestCase):
    def test_owner_aggregation_reconciles_supply(self):
        mint = mint_evidence(TOKEN, mint_result(), NOW)
        data = accounts_result()
        data['value'][1]['account']['data']['parsed']['info']['owner'] = key(21)
        owners = owner_evidence(TOKEN, mint, data, NOW)
        self.assertEqual(owners['top_owner_pct'], 10)
        self.assertEqual(owners['top10_owner_pct'], 55)
        self.assertEqual(owners['owner_count'], 19)
        self.assertEqual(owners['accounted_supply'], '10000')
        self.assertEqual(owners['exclusions'], [])

    def test_partial_duplicate_or_frozen_results_rejected(self):
        mint = mint_evidence(TOKEN, mint_result(), NOW)
        for change in ('partial','duplicate','frozen','wrong_mint','old_slot'):
            data = accounts_result()
            if change == 'partial':
                data['value'].pop()
            elif change == 'duplicate':
                data['value'][1]['pubkey'] = data['value'][0]['pubkey']
            elif change == 'old_slot':
                data['context']['slot'] = 99
            else:
                info = data['value'][0]['account']['data']['parsed']['info']
                info['state' if change == 'frozen' else 'mint'] = 'frozen' if change == 'frozen' else USDC
            with self.subTest(change=change), self.assertRaises(ValueError):
                owner_evidence(TOKEN, mint, data, NOW)

    def test_mint_fields_fail_closed(self):
        for change in ('missing','token2022','uninitialized','no_supply'):
            data = mint_result()
            info = data['value']['data']['parsed']['info']
            if change == 'missing':
                del info['mintAuthority']
            elif change == 'token2022':
                data['value']['owner'] = TOKEN_2022_PROGRAM
            elif change == 'uninitialized':
                info['isInitialized'] = False
            else:
                info['supply'] = '0'
            with self.subTest(change=change), self.assertRaises(ValueError):
                mint_evidence(TOKEN, data, NOW)

    def test_read_only_rpc_filter_and_authority_shortcut(self):
        recorder = Mock()
        http = Mock()
        http.request.side_effect = [({'id':1,'result':mint_result()}, NOW),
                                    ({'id':1,'result':accounts_result()}, NOW)]
        rpc = SolanaRPC(recorder, http)
        mint, owners = rpc.inspect(TOKEN)
        payload = http.request.call_args_list[1].kwargs['payload']
        self.assertEqual(payload['method'], 'getProgramAccounts')
        self.assertEqual(payload['params'][1]['filters'][1]['memcmp']['bytes'], TOKEN)
        self.assertEqual(payload['params'][1]['minContextSlot'], 100)
        self.assertIsNotNone(owners)
        with self.assertRaises(ValueError):
            rpc.call('sendTransaction', [])
        data = mint_result()
        data['value']['data']['parsed']['info']['freezeAuthority'] = USDC
        http.reset_mock()
        http.request.side_effect = [({'id':1,'result':data}, NOW)]
        _, owners = rpc.inspect(TOKEN)
        self.assertIsNone(owners)
        self.assertEqual(http.request.call_count, 1)

    def test_address_is_actual_32_byte_key(self):
        self.assertEqual(address(TOKEN), TOKEN)
        for invalid in ('A'*32, '0'*44, '1'*33, ''):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                address(invalid)

    def test_token2022_metadata_subset_and_account_sizes(self):
        result = mint_result()
        result['value']['owner'] = TOKEN_2022_PROGRAM
        result['value']['data']['space'] = 425
        info = result['value']['data']['parsed']['info']
        info['extensions'] = [
            {'extension':'metadataPointer','state':{'authority':None,'metadataAddress':TOKEN}},
            {'extension':'tokenMetadata','state':{'updateAuthority':None,'mint':TOKEN}}]
        mint = mint_evidence(TOKEN, result, NOW)
        self.assertEqual(mint['program'], TOKEN_2022_PROGRAM)
        accounts = accounts_result()
        for row in accounts['value']:
            row['account']['owner'] = TOKEN_2022_PROGRAM
            row['account']['data']['space'] = 170
            row['account']['data']['parsed']['info']['extensions'] = [{'extension':'immutableOwner'}]
        self.assertEqual(owner_evidence(TOKEN, mint, accounts, NOW)['account_count'], 20)
        http = Mock()
        http.request.side_effect = [({'id':1,'result':result},NOW), ({'id':1,'result':accounts},NOW)]
        SolanaRPC(Mock(), http).inspect(TOKEN)
        params = http.request.call_args_list[1].kwargs['payload']['params']
        self.assertEqual(params[0], TOKEN_2022_PROGRAM)
        self.assertFalse(any('dataSize' in item for item in params[1]['filters']))

    def test_metadata_mutability_identity_and_missing_are_distinct(self):
        from desk.onchain import UnsupportedTokenFeature, MetadataIdentityMismatch
        for name,identity,authority in (('tokenMetadata','mint','updateAuthority'),('metadataPointer','metadataAddress','authority')):
            result=mint_result()
            result['value']['owner']=TOKEN_2022_PROGRAM
            state={identity:TOKEN,authority:USDC}
            result['value']['data']['parsed']['info']['extensions']=[{'extension':name,'state':state}]
            with self.assertRaises(UnsupportedTokenFeature):mint_evidence(TOKEN,result,NOW)
            self.assertEqual(mint_evidence(TOKEN,result,NOW,allow_mutable_metadata=True)['metadata_authorities'][name],USDC)
            state[identity]=USDC
            with self.assertRaises(MetadataIdentityMismatch):mint_evidence(TOKEN,result,NOW)
            with self.assertRaises(MetadataIdentityMismatch):mint_evidence(TOKEN,result,NOW,allow_mutable_metadata=True)
            state[identity]=TOKEN
            del state[authority]
            with self.assertRaisesRegex(ValueError,'missing'):mint_evidence(TOKEN,result,NOW)
            state[authority]=None
            self.assertIn(name,mint_evidence(TOKEN,result,NOW)['extensions'])

    def test_unknown_and_restrictive_extensions_are_rejected(self):
        for extension in ('transferFeeConfig','transferHook','permanentDelegate','defaultAccountState',
                          'confidentialTransferMint','nonTransferable','newUnknownFeature'):
            result = mint_result()
            result['value']['owner'] = TOKEN_2022_PROGRAM
            result['value']['data']['parsed']['info']['extensions'] = [{'extension':extension}]
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                mint_evidence(TOKEN, result, NOW)


class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.rules = replace(Rules(), ticket=5, max_top10_owner_pct=60, require_preflight=True)

    def test_roundtrip_sizes_sell_from_minimum_buy_output(self):
        http = Mock()
        http.request.side_effect = [(order(), NOW), (order(TOKEN, USDC, 24750000, 5000000), NOW)]
        jupiter = Jupiter(Mock(), http)
        result = jupiter.roundtrip(TOKEN, 5, 100)
        self.assertEqual(result['sell']['input_amount'], result['buy']['minimum_output'])
        self.assertAlmostEqual(result['worst_quote_roundtrip_loss_pct'], 1)
        self.assertEqual(quote_reasons(TOKEN, result, self.rules, NOW), [])
        for call in http.request.call_args_list:
            url = call.args[0]
            self.assertIn('/swap/v2/order?', url)
            self.assertNotIn('taker=', url)
            self.assertNotIn('execute', url)
            self.assertNotIn('payload', call.kwargs)

    def test_invalid_order_bounds_and_identity(self):
        for update in ({'inAmount':'4'}, {'inputMint':TOKEN}, {'outAmount':'NaN'},
                       {'routePlan':[]}, {'transaction':'unsigned-transaction'}, {'errorCode':1},
                       {'otherAmountThreshold':'1'}, {'feeBps':float('nan')}, {'priceImpact':float('inf')}):
            data = order()
            data.update(update)
            with self.subTest(update=update), self.assertRaises((ValueError, KeyError, TypeError)):
                parse_quote(data, USDC, TOKEN, 5000000, NOW)

    def test_freshness_costs_and_expiry(self):
        q = quote_evidence()
        self.assertEqual(quote_reasons(TOKEN, q, self.rules, NOW), [])
        self.assertIn('buy_quote_stale', quote_reasons(TOKEN, q, self.rules, NOW+31))
        q['buy'].update(price_impact_pct=2, fee_bps=100, slippage_bps=101, expire_at=NOW)
        q['sell']['minimum_output'] = '4800000'
        q['network_lamports'] = 6000000
        reasons = quote_reasons(TOKEN, q, self.rules, NOW)
        for reason in ('buy_price_impact_limit','buy_fee_limit','buy_slippage_limit',
                       'buy_quote_expired','roundtrip_loss_limit','quote_network_cost_limit'):
            self.assertIn(reason, reasons)

    def test_full_preflight_gate_and_authorities(self):
        e = evidence()
        self.assertEqual(preflight_reasons(TOKEN, e, self.rules, NOW), [])
        s = sample(token=TOKEN, holders=e['holders'], preflight=e)
        self.assertEqual(vet(s, self.rules, NOW), [])
        e['mint']['freeze_authority'] = USDC
        self.assertIn('freeze_authority_not_revoked', vet(s, self.rules, NOW))
        self.assertTrue(vet(sample(token=TOKEN), self.rules, NOW))

    def test_unsupported_feature_is_distinct_from_provider_failure(self):
        from desk.onchain import UnsupportedTokenFeature
        from desk.filters import report
        rpc,jupiter=Mock(),Mock()
        rpc.inspect.side_effect=UnsupportedTokenFeature('unsupported Token-2022 extension: transferFeeConfig')
        jupiter.roundtrip.return_value=evidence()['quotes']
        result=collect(TOKEN,self.rules,rpc,jupiter)
        self.assertIn('unsupported_token_features',result['errors'])
        self.assertNotIn('onchain_evidence_unavailable',result['errors'])
        self.assertFalse(result['screen_passed'])
        for field in ('mint_authority','freeze_authority'):
            self.assertIn(field+'_evidence_missing',result['reasons'])
            self.assertNotIn(field+'_not_revoked',result['reasons'])
        measured=report(sample(token=TOKEN,preflight=result),self.rules,NOW)
        feature=next(c for c in measured['checks'] if c['check']=='supported_token_feature_validation')
        self.assertEqual(feature['status'],'fail')
        self.assertIn('transferFeeConfig',measured['evidence_details']['onchain_error'])
        for field in ('mint_authority','freeze_authority'):
            c=next(c for c in measured['checks'] if c['check']==field)
            self.assertEqual(c['status'],'missing')
            self.assertEqual(c['reason'],field+'_evidence_missing')

    def test_missing_authority_still_blocks_and_active_is_explicit(self):
        e=evidence()
        del e['mint']['mint_authority']
        reasons=preflight_reasons(TOKEN,e,self.rules,NOW)
        self.assertIn('mint_authority_evidence_missing',reasons)
        self.assertNotIn('mint_authority_not_revoked',reasons)
        e['mint']['mint_authority']=USDC
        self.assertIn('mint_authority_not_revoked',preflight_reasons(TOKEN,e,self.rules,NOW))

    def test_provider_failure_is_a_recordable_rejection(self):
        rpc, jupiter = Mock(), Mock()
        rpc.inspect.side_effect = RuntimeError('provider network request failed')
        jupiter.roundtrip.side_effect = RuntimeError('provider HTTP 401')
        result = collect(TOKEN, self.rules, rpc, jupiter)
        self.assertFalse(result['screen_passed'])
        self.assertFalse(result['ready_for_live'])
        with tempfile.TemporaryDirectory() as folder:
            desk = Desk(Path(folder)/'db.sqlite', self.rules)
            run = desk.run('check', NOW)
            desk.preflight(run, result)
            self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM preflights').fetchone()[0], 1)
            self.assertEqual(desk.report()['fills'], 0)
            desk.close()

    def test_gated_paper_entry_cannot_skip_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            desk = Desk(Path(folder)/'db.sqlite', self.rules)
            run = desk.run('test', NOW)
            desk.record(run, sample(token=TOKEN), NOW)
            self.assertEqual(desk.report(NOW)['fills'], 0)
            e = evidence()
            e['mint']['observed_at'] += 1
            e['holders']['observed_at'] += 1
            desk.record(run, sample(token=TOKEN, now=NOW+1, holders=e['holders'], preflight=e), NOW+1)
            self.assertEqual(desk.report(NOW+1)['fills'], 1)
            desk.close()

    def test_pilot_evidence_can_replay_without_network(self):
        e = evidence()
        event = dict(observed_at=NOW, holders=e['holders'], preflight=e,
                     pair=dict(chainId='solana', baseToken=dict(address=TOKEN, symbol='TEST'),
                               pairAddress='test_pool', priceUsd='1', liquidity=dict(usd=100000),
                               volume=dict(h24=40000, h6=10000), pairCreatedAt=(NOW-86400)*1000))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'events.json'
            path.write_text(json.dumps([event]))
            desk = Desk(Path(folder)/'db.sqlite', self.rules)
            report = fixture(desk, path)
            self.assertEqual(report['fills'], 1)
            self.assertEqual(fixture(desk, path)['fills'], 1)
            stored = json.loads(desk.db.execute('SELECT data FROM snapshots LIMIT 1').fetchone()[0])
            self.assertEqual(stored['preflight'], e)
            desk.close()


class HttpTests(unittest.TestCase):
    def test_http_errors_do_not_expose_provider_credentials(self):
        http = JsonHTTP(attempts=1, spacing=0)
        http.opener.open = Mock(side_effect=HTTPError('https://provider/?secret=abc', 401, 'secret=abc', {}, None))
        with self.assertRaisesRegex(RuntimeError, '^provider HTTP 401$'):
            http.request('https://provider/?secret=abc')

    def test_bounded_and_nonfinite_json_rejected(self):
        for raw, limit in ((b'123456789', 5), (b'{"v":NaN}', 100)):
            http = JsonHTTP(attempts=1, spacing=0, max_bytes=limit)
            response = Mock()
            response.read.return_value = raw
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            http.opener.open = Mock(return_value=context)
            with self.assertRaises(ValueError):
                http.request('https://provider/')


if __name__ == '__main__':
    unittest.main()
