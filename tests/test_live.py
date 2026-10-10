import base64
import copy
import hashlib
import importlib.util
import json
import os
import struct
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from desk.core import Desk, Rules
from desk.live import LiveBook, LiveEngine, LiveLimits
from desk.live_jupiter import LiveJupiter
from desk.live_rpc import ATA_PROGRAM, SYSTEM, LiveRPC, ata
from desk.onchain import TOKEN_PROGRAM, TOKEN_2022_PROGRAM
from desk.quotes import USDC
from desk.transaction import COMPUTE_PROGRAM, JUPITER_PROGRAM, LOOKUP_PROGRAM, ROUTE, inspect, simulation_effects
from desk.wallet import create_wallet, load_wallet, sdk
from test_preflight import TOKEN, evidence, mint_result
from test_desk import sample

HAS_SDK = importlib.util.find_spec('solders') is not None


class LiveJupiterTests(unittest.TestCase):
    def setUp(self):
        self.quote = dict(inputMint=USDC,outputMint=TOKEN,inAmount='5000000',outAmount='43040960',
                          otherAmountThreshold='42610551',swapMode='ExactIn',slippageBps=100,
                          routePlan=[{'swapInfo':{'label':'test'}}],priceImpactPct='0.001',platformFee=None)
        self.order = dict(swapTransaction='unsigned-placeholder',lastValidBlockHeight=123,
                          prioritizationFeeLamports=100000,simulationError=None)
        self.http = Mock()
        self.http.request.side_effect = [(self.quote,100),(self.order,101)]
        self.recorder = Mock()
        self.client = LiveJupiter(self.recorder,self.http)

    def test_builder_rounds_minimum_up_and_requests_fixed_limits(self):
        result = self.client.build(TOKEN,USDC,TOKEN,5000000,LiveLimits())
        self.assertEqual(result['quote']['otherAmountThreshold'],'42610551')
        self.assertEqual(result['unsigned_transaction'],'unsigned-placeholder')
        url = self.http.request.call_args_list[0].args[0]
        self.assertIn('instructionVersion=V1',url)
        self.assertIn('swapMode=ExactIn',url)
        payload = self.http.request.call_args_list[1].kwargs['payload']
        self.assertFalse(payload['dynamicSlippage'])
        self.assertFalse(payload['wrapAndUnwrapSol'])
        self.assertEqual(payload['prioritizationFeeLamports']['priorityLevelWithMaxLamports']['maxLamports'],100000)
        self.assertEqual(self.recorder.call_count,2)

    def test_floor_minimum_is_rejected_before_build(self):
        self.quote['otherAmountThreshold'] = '42610550'
        with self.assertRaisesRegex(ValueError,'output bounds'):
            self.client.build(TOKEN,USDC,TOKEN,5000000,LiveLimits())
        self.assertEqual(self.http.request.call_count,1)

    def test_failed_provider_simulation_is_rejected(self):
        self.order['simulationError'] = {'error':'insufficient funds'}
        with self.assertRaisesRegex(ValueError,'build and simulate'):
            self.client.build(TOKEN,USDC,TOKEN,5000000,LiveLimits())

    def test_platform_fee_is_rejected_before_build(self):
        self.quote['platformFee'] = {'amount':'1','feeBps':1}
        with self.assertRaisesRegex(ValueError,'platform/referral'):
            self.client.build(TOKEN,USDC,TOKEN,5000000,LiveLimits())
        self.assertEqual(self.http.request.call_count,1)

    def test_excess_priority_fee_is_rejected(self):
        self.order['prioritizationFeeLamports'] = 100001
        with self.assertRaisesRegex(ValueError,'priority fee'):
            self.client.build(TOKEN,USDC,TOKEN,5000000,LiveLimits())


def native(amount):
    return dict(lamports=amount,owner=SYSTEM,executable=False,data=['','base64'],rentEpoch=0,space=0)


def account(wallet, mint, amount):
    _,Pubkey,_,_ = sdk()
    data = bytearray(165)
    data[:32] = bytes(Pubkey.from_string(mint))
    data[32:64] = bytes(Pubkey.from_string(wallet))
    data[64:72] = amount.to_bytes(8,'little')
    data[108] = 1
    if mint == TOKEN:
        data[109:113] = (1).to_bytes(4,'little')
        data[113:121] = (2039280).to_bytes(8,'little')
    return dict(lamports=2039280+(amount if mint == TOKEN else 0),owner=TOKEN_PROGRAM,executable=False,
                data=[base64.b64encode(data).decode(),'base64'],rentEpoch=0,space=165)


def built_transaction(signer,input_ata,output_ata,quote,extra=(),amount_override=None):
    from solders.hash import Hash
    from solders.message import MessageV0
    from solders.instruction import AccountMeta,Instruction
    from solders.signature import Signature
    from solders.transaction import VersionedTransaction
    from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
    _,Pubkey,_,_ = sdk()
    keys = [TOKEN_PROGRAM,str(signer.pubkey()),input_ata,output_ata,JUPITER_PROGRAM,quote['outputMint'],JUPITER_PROGRAM]
    metas = [AccountMeta(Pubkey.from_string(key),index==1,index in (1,2,3)) for index,key in enumerate(keys)]
    data = ROUTE+struct.pack('<I',1)+bytes([0,100,0,1])+struct.pack('<QQHB',
          int(quote['inAmount']) if amount_override is None else amount_override,int(quote['outAmount']),100,0)
    route = Instruction(Pubkey.from_string(JUPITER_PROGRAM),data,metas)
    msg = MessageV0.try_compile(signer.pubkey(),[set_compute_unit_limit(200000),set_compute_unit_price(100000),route,*extra],[],Hash.new_unique())
    tx = VersionedTransaction.populate(msg,[Signature.default()])
    return base64.b64encode(bytes(tx)).decode()


@unittest.skipUnless(HAS_SDK,'optional solders SDK not installed')
class WalletTests(unittest.TestCase):
    def test_create_private_wallet_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'wallet.json'
            result = create_wallet(path)
            self.assertFalse(result['funded'])
            self.assertEqual(os.stat(path).st_mode & 0o777,0o600)
            self.assertEqual(str(load_wallet(path).pubkey()),result['address'])
            self.assertNotIn('secret',json.dumps(result))
            with self.assertRaises(FileExistsError):
                create_wallet(path)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                load_wallet(path)

    def test_symlink_wallet_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'wallet.json'
            create_wallet(path)
            link = Path(folder)/'link.json'
            link.symlink_to(path)
            with self.assertRaises(OSError):
                load_wallet(link)


@unittest.skipUnless(HAS_SDK,'optional solders SDK not installed')
class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name)/'live.sqlite'
        Keypair,_,_,_ = sdk()
        self.signer = Keypair()
        self.wallet = str(self.signer.pubkey())
        self.desk = Desk(self.db_path,replace(Rules(),ticket=5,max_top10_owner_pct=60))
        self.book = LiveBook(self.desk,self.wallet)
        self.book.kill(False)
        self.input_ata = ata(self.wallet,USDC,TOKEN_PROGRAM)
        self.output_ata = ata(self.wallet,TOKEN,TOKEN_PROGRAM)
        self.before = dict(wallet=self.wallet,slot=100,sol_lamports=20000000,balances={USDC:10000000},accounts={
            self.wallet:native(20000000),self.input_ata:account(self.wallet,USDC,10000000),self.output_ata:None})
        self.after = copy.deepcopy(self.before)
        self.after.update(slot=101,sol_lamports=17935720,balances={USDC:5000000,TOKEN:25000000})
        self.after['accounts'] = {self.wallet:native(17935720),self.input_ata:account(self.wallet,USDC,5000000),
                                 self.output_ata:account(self.wallet,TOKEN,25000000)}
        self.quote = dict(inputMint=USDC,outputMint=TOKEN,inAmount='5000000',outAmount='25000000',
                          otherAmountThreshold='24750000',slippageBps=100)
        self.b64 = built_transaction(self.signer,self.input_ata,self.output_ata,self.quote)
        self.built = dict(quote=self.quote,unsigned_transaction=self.b64,observed_at=time.time(),built_at=time.time(),last_valid_block_height=200)
        self.rpc,self.builder,self.scanner,self.token_rpc,self.quote_client = [Mock() for _ in range(5)]
        self.rpc.snapshot.return_value = self.before
        self.rpc.simulate.return_value = dict(slot=101,accounts=self.after['accounts'])
        self.rpc.call.side_effect = self.rpc_call
        self.sent = None
        def send(value):
            self.sent = value
            _,_,VersionedTransaction,_ = sdk()
            return str(VersionedTransaction.from_bytes(base64.b64decode(value)).signatures[0])
        self.rpc.send.side_effect = send
        self.builder.build.return_value = self.built
        e = evidence()
        now = time.time()
        e['mint']['observed_at'] = e['holders']['observed_at'] = now
        for leg in ('buy','sell'):
            e['quotes'][leg]['observed_at'] = now
        self.token_rpc.inspect.return_value = (e['mint'],e['holders'])
        self.quote_client.roundtrip.return_value = e['quotes']
        self.scanner.pairs.return_value = [sample(token=TOKEN,now=now,created_at=now-86400)]
        self.engine = LiveEngine(self.book,self.signer,self.rpc,self.builder,self.scanner,self.token_rpc,self.quote_client)
        self.chain = None
        self.status = None
        self.height = 100

    def rpc_call(self,method,params):
        if method == 'getFeeForMessage':
            return dict(value=25000)
        if method == 'getBlockHeight':
            return self.height
        if method == 'getSignatureStatuses':
            return dict(value=[self.status])
        if method == 'getTransaction':
            return self.chain
        if method == 'isBlockhashValid':
            return dict(value=False)
        raise AssertionError('unexpected RPC method '+method)

    def tearDown(self):
        self.desk.close()
        self.tmp.cleanup()

    def prepare(self):
        return self.engine.prepare(TOKEN,'buy')

    def submit(self):
        review = self.prepare()
        result = self.engine.submit(review['order_id'],review['approval_hash'])
        return review,result

    def finalize(self,review,err=None):
        row = self.book.order(review['order_id'])
        payload = json.loads(row['data'])
        keys = payload['inspection']['account_keys']
        pre = [0]*len(keys)
        post = [0]*len(keys)
        pre[0],post[0] = self.before['sol_lamports'],self.after['sol_lamports']
        def balance(account_key,mint,amount):
            return dict(accountIndex=keys.index(account_key),owner=self.wallet,mint=mint,uiTokenAmount=dict(amount=str(amount)))
        self.chain = dict(transaction=[self.sent,'base64'],blockTime=int(time.time()),
            meta=dict(err=err,fee=25000,preBalances=pre,postBalances=post,
                      loadedAddresses=dict(writable=[],readonly=[]),
                      preTokenBalances=[balance(self.input_ata,USDC,10000000)],
                      postTokenBalances=[balance(self.input_ata,USDC,5000000),balance(self.output_ata,TOKEN,25000000)]))
        self.status = dict(confirmationStatus='finalized',err=err)
        self.rpc.snapshot.return_value = self.after

    def test_prepare_does_not_sign_or_send(self):
        review = self.prepare()
        self.assertEqual(review['state'],'prepared')
        self.assertFalse(review['submitted'])
        self.assertEqual(review['input_amount'],'5000000')
        self.assertEqual(review['minimum_output'],'24750000')
        self.assertFalse(self.rpc.send.called)
        self.assertIsNone(self.book.order(review['order_id'])['signature'])
        self.assertEqual(self.desk.report()['fills'],0)
        self.assertEqual(len(self.book.positions()),0)

    def test_wrong_approval_never_submits(self):
        review = self.prepare()
        with self.assertRaises(ValueError):
            self.engine.submit(review['order_id'],'wrong')
        self.assertFalse(self.rpc.send.called)
        self.assertEqual(self.book.order(review['order_id'])['state'],'prepared')

    def test_payload_tampering_never_submits(self):
        review = self.prepare()
        data = json.loads(self.book.order(review['order_id'])['data'])
        data['built']['quote']['inAmount'] = '1000000000'
        with self.book.db:
            self.book.db.execute('UPDATE live_orders SET data=? WHERE id=?',(json.dumps(data),review['order_id']))
        with self.assertRaises(ValueError):
            self.engine.submit(review['order_id'],review['approval_hash'])
        self.assertFalse(self.rpc.send.called)

    def test_order_staleness_never_submits(self):
        self.built['observed_at'] -= 31
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.rpc.send.called)

    def test_order_blocks_duplicate_prepare(self):
        review = self.prepare()
        with self.assertRaises(ValueError):
            self.prepare()
        self.book.cancel(review['order_id'])
        self.assertEqual(self.prepare()['state'],'prepared')

    def test_kill_switch_blocks_entry_at_submission(self):
        review = self.prepare()
        self.book.kill(True)
        with self.assertRaises(ValueError):
            self.engine.submit(review['order_id'],review['approval_hash'])
        self.assertFalse(self.rpc.send.called)

    def test_changed_wallet_never_submits(self):
        review = self.prepare()
        changed = copy.deepcopy(self.before)
        changed['accounts'][self.wallet]['lamports'] -= 1
        self.rpc.snapshot.return_value = changed
        with self.assertRaises(ValueError):
            self.engine.submit(review['order_id'],review['approval_hash'])
        self.assertFalse(self.rpc.send.called)

    def test_timeout_persists_signature_and_blocks_second_trade(self):
        review = self.prepare()
        def timeout(transaction):
            # The journal must already contain the exact signature at this boundary.
            self.assertEqual(self.book.order(review['order_id'])['state'],'submitting')
            self.assertIsNotNone(self.book.order(review['order_id'])['signature'])
            raise RuntimeError('network timeout')
        self.rpc.send.side_effect = timeout
        result = self.engine.submit(review['order_id'],review['approval_hash'])
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(self.rpc.send.call_count,1)
        with self.assertRaises(ValueError):
            self.engine.submit(review['order_id'],review['approval_hash'])
        with self.assertRaises(ValueError):
            self.prepare()
        with self.assertRaises(ValueError):
            self.book.cancel(review['order_id'])
        self.desk.close()
        self.desk = Desk(self.db_path)
        self.book = LiveBook(self.desk,self.wallet)
        self.assertEqual(self.book.order(review['order_id'])['state'],'unknown')
        self.assertEqual(self.book.order(review['order_id'])['signature'],result['signature'])

    def test_finalized_receipt_updates_position_once(self):
        review,_ = self.submit()
        self.finalize(review)
        result = self.engine.recover(review['order_id'])
        self.assertEqual(result['state'],'confirmed')
        self.assertEqual(len(self.book.positions()),1)
        self.assertEqual(self.book.positions()[0]['quantity'],'25000000')
        self.assertEqual(self.book.positions()[0]['cost_usdc'],'5')
        self.assertEqual(self.engine.recover(review['order_id'])['state'],'confirmed')
        self.assertEqual(len(self.book.positions()),1)
        self.assertEqual(self.book.db.execute('SELECT COUNT(*) FROM live_settlements').fetchone()[0],1)
        self.assertEqual(self.desk.report()['fills'],0)

    def test_confirmation_without_finalized_metadata_keeps_order_pending(self):
        review,_ = self.submit()
        self.status = dict(confirmationStatus='confirmed',err=None)
        result = self.engine.recover(review['order_id'])
        self.assertEqual(result['state'],'submitted')
        self.assertFalse(self.book.positions())

    def test_external_balance_change_blocks_reconciliation(self):
        review,_ = self.submit()
        self.finalize(review)
        current = copy.deepcopy(self.after)
        current['balances'][USDC] += 1
        self.rpc.snapshot.return_value = current
        result = self.engine.recover(review['order_id'])
        self.assertEqual(result['state'],'reconciliation_required')
        self.assertEqual(self.book.status()['kill_switch'],'on')
        self.assertFalse(self.book.positions())

    def test_minimum_output_violation_blocks_reconciliation(self):
        review,_ = self.submit()
        self.finalize(review)
        self.chain['meta']['postTokenBalances'][1]['uiTokenAmount']['amount'] = '1'
        self.assertEqual(self.engine.recover(review['order_id'])['state'],'reconciliation_required')

    def test_expiry_requires_no_status_no_transaction_and_unchanged_balances(self):
        review,_ = self.submit()
        self.height = 233
        result = self.engine.recover(review['order_id'])
        self.assertEqual(result['state'],'expired')
        self.assertEqual(self.rpc.send.call_count,1)
        self.assertFalse(self.book.positions())

    def test_expiry_with_balance_drift_remains_blocked(self):
        review,_ = self.submit()
        self.height = 233
        changed = copy.deepcopy(self.before)
        changed['accounts'][self.wallet]['lamports'] -= 5000
        self.rpc.snapshot.return_value = changed
        self.assertEqual(self.engine.recover(review['order_id'])['state'],'reconciliation_required')

    def test_daily_loss_blocks_entries_but_allows_sell_under_kill_switch(self):
        with self.book.db:
            self.book.db.execute("INSERT INTO live_positions(wallet,token,quantity,cost_usdc,opened_at,status) VALUES (?,?,?,?,?,'open')",
                                 (self.wallet,TOKEN,'25000000','5',time.time()))
        self.book.kill(True)
        self.book.risk('sell',TOKEN,self.after)
        # Use a closed settlement to trigger daily entry guard with no open position.
        with self.book.db:
            self.book.db.execute("UPDATE live_positions SET status='closed'")
        order_id = self.book.reserve(TOKEN,'buy')
        with self.book.db:
            self.book.db.execute("UPDATE live_orders SET state='failed' WHERE id=?",(order_id,))
            self.book.db.execute('INSERT INTO live_settlements VALUES (?,?,?,?,?)',(order_id,time.time(),'-2',0,'{}'))
        self.book.kill(False)
        with self.assertRaisesRegex(ValueError,'daily'):
            self.book.risk('buy',TOKEN,self.before)

    def test_buy_then_sell_reconciles_real_amounts_and_pnl(self):
        review,_ = self.submit()
        self.finalize(review)
        self.engine.recover(review['order_id'])
        self.book.kill(True)  # Emergency entry halt must not prevent closing.
        self.token_rpc.call.return_value = (mint_result(),time.time())
        sell_quote = dict(inputMint=TOKEN,outputMint=USDC,inAmount='25000000',outAmount='5500000',
                          otherAmountThreshold='5445000',slippageBps=100)
        sell_b64 = built_transaction(self.signer,self.output_ata,self.input_ata,sell_quote)
        self.builder.build.return_value = dict(quote=sell_quote,unsigned_transaction=sell_b64,
            observed_at=time.time(),built_at=time.time(),last_valid_block_height=200)
        after_sell = copy.deepcopy(self.after)
        after_sell['sol_lamports'] -= 25000
        after_sell['balances'] = {USDC:10500000}
        after_sell['accounts'] = {self.wallet:native(after_sell['sol_lamports']),
            self.input_ata:account(self.wallet,USDC,10500000),self.output_ata:account(self.wallet,TOKEN,0)}
        self.rpc.snapshot.return_value = self.after
        self.rpc.simulate.return_value = dict(slot=103,accounts=after_sell['accounts'])
        sell_review = self.engine.prepare(TOKEN,'sell')
        self.engine.submit(sell_review['order_id'],sell_review['approval_hash'])
        payload = json.loads(self.book.order(sell_review['order_id'])['data'])
        keys = payload['inspection']['account_keys']
        def row(key,mint,amount):
            return dict(accountIndex=keys.index(key),owner=self.wallet,mint=mint,uiTokenAmount=dict(amount=str(amount)))
        pre,post = [0]*len(keys),[0]*len(keys)
        pre[0],post[0] = self.after['sol_lamports'],after_sell['sol_lamports']
        self.chain = dict(transaction=[self.sent,'base64'],blockTime=int(time.time()),meta=dict(err=None,fee=25000,
            preBalances=pre,postBalances=post,loadedAddresses=dict(writable=[],readonly=[]),
            preTokenBalances=[row(self.input_ata,USDC,5000000),row(self.output_ata,TOKEN,25000000)],
            postTokenBalances=[row(self.input_ata,USDC,10500000),row(self.output_ata,TOKEN,0)]))
        self.rpc.snapshot.return_value = after_sell
        result = self.engine.recover(sell_review['order_id'])
        self.assertEqual(result['state'],'confirmed')
        self.assertEqual(result['pnl_usdc'],'0.5')
        self.assertFalse(self.book.positions())
        self.assertEqual(self.book.db.execute('SELECT COUNT(*) FROM live_settlements').fetchone()[0],2)

    def test_finalized_failure_records_fee_without_position(self):
        review,_ = self.submit()
        self.finalize(review,err={'InstructionError':[2,'Custom']})
        meta = self.chain['meta']
        meta['postBalances'][0] = self.before['sol_lamports']-25000
        meta['postTokenBalances'] = copy.deepcopy(meta['preTokenBalances'])
        current = copy.deepcopy(self.before)
        current['sol_lamports'] -= 25000
        current['accounts'][self.wallet]['lamports'] -= 25000
        self.rpc.snapshot.return_value = current
        result = self.engine.recover(review['order_id'])
        self.assertEqual(result['state'],'failed')
        self.assertFalse(self.book.positions())
        self.assertEqual(self.book.db.execute('SELECT sol_cost_lamports FROM live_settlements').fetchone()[0],25000)

    def test_wallet_caps_and_exit_fee_reservation(self):
        for change in ('too_much_sol','too_much_usdc','no_exit_fee_reserve'):
            snapshot = copy.deepcopy(self.before)
            if change == 'too_much_sol':
                snapshot['sol_lamports'] = 31000000
            elif change == 'too_much_usdc':
                snapshot['balances'][USDC] = 51000000
            else:
                snapshot['sol_lamports'] = 10000000
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.book.risk('buy',TOKEN,snapshot)

    def test_wrong_signer_amount_and_top_level_transfer_rejected(self):
        from solders.system_program import transfer,TransferParams
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        wrong = Keypair()
        with self.assertRaises(ValueError):
            inspect(self.b64,str(wrong.pubkey()),self.input_ata,self.output_ata,USDC,TOKEN,self.quote,LiveLimits(),self.rpc)
        for malicious in (built_transaction(self.signer,self.input_ata,self.output_ata,self.quote,amount_override=6000000),
                          built_transaction(self.signer,self.input_ata,self.output_ata,self.quote,extra=[transfer(TransferParams(
                              from_pubkey=self.signer.pubkey(),to_pubkey=wrong.pubkey(),lamports=1))])):
            with self.assertRaises(ValueError):
                inspect(malicious,self.wallet,self.input_ata,self.output_ata,USDC,TOKEN,self.quote,LiveLimits(),self.rpc)

    def test_simulation_detects_authority_changes_and_wrong_amounts(self):
        for change in ('authority','amount','sol_budget'):
            simulation = dict(accounts=copy.deepcopy(self.after['accounts']))
            if change == 'authority':
                raw = bytearray(base64.b64decode(simulation['accounts'][self.input_ata]['data'][0]))
                raw[72] = 1
                simulation['accounts'][self.input_ata]['data'][0] = base64.b64encode(raw).decode()
            elif change == 'amount':
                simulation['accounts'][self.input_ata] = account(self.wallet,USDC,4000000)
            else:
                simulation['accounts'][self.wallet]['lamports'] = 10000000
            with self.subTest(change=change),self.assertRaises(ValueError):
                simulation_effects(self.before,simulation,self.input_ata,self.output_ata,self.quote,LiveLimits())

    def test_two_connections_cannot_reserve_two_intents(self):
        def worker(_):
            desk = Desk(self.db_path)
            try:
                book = LiveBook(desk,self.wallet)
                try:
                    return book.reserve(TOKEN,'buy')
                except ValueError:
                    return None
            finally:
                desk.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker,range(2)))
        self.assertEqual(sum(result is not None for result in results),1)

    def test_rpc_submission_does_not_share_retry_transport(self):
        read,submit = Mock(),Mock()
        submit.request.return_value = ({'id':1,'result':'signature'},time.time())
        rpc = LiveRPC(Mock(),read,submit_http=submit)
        self.assertEqual(rpc.send('signed'),'signature')
        self.assertFalse(read.request.called)
        self.assertEqual(submit.request.call_count,1)
        self.assertEqual(submit.request.call_args.kwargs['payload']['params'][1]['maxRetries'],0)
        with self.assertRaises(ValueError):
            rpc.call('sendTransaction',[])

    def test_native_execution_test_is_explicit_and_canonical_only(self):
        data = mint_result()
        data['value']['data']['parsed']['info'].update(supply='0',decimals=9)
        self.token_rpc.call.return_value = (data,time.time())
        review = self.engine.prepare(TOKEN,'buy',execution_test=True)
        self.assertTrue(review['execution_test'])
        self.assertFalse(self.scanner.pairs.called)
        self.assertFalse(self.rpc.send.called)
        self.book.cancel(review['order_id'])
        with self.assertRaises(ValueError):
            self.engine.prepare(USDC,'buy',execution_test=True)
        data['value']['data']['parsed']['info']['mintAuthority'] = USDC
        with self.assertRaises(ValueError):
            self.engine.prepare(TOKEN,'buy',execution_test=True)

    def test_live_rpc_rejects_wrong_network_fingerprint(self):
        http = Mock()
        http.request.return_value = ({'id':1,'result':'5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'},time.time())
        rpc = LiveRPC(Mock(),http)
        rpc.mainnet()
        http.request.return_value = ({'id':1,'result':'5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp'},time.time())
        with self.assertRaises(ValueError):
            rpc.mainnet()


if __name__ == '__main__':
    unittest.main()
