"""Manual live pilot with durable intent, reviewed submission and canonical reconciliation."""
import base64
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal

from .core import encode, money, number, vet
from .live_rpc import ata, raw_data, token_account
from .onchain import TOKEN_PROGRAM, address, uint, parsed_account, extensions
from .preflight import collect, quote_reasons
from .quotes import USDC, WRAPPED_SOL
from .scanner import best_pair
from .transaction import decode, inspect, simulation_effects
from .wallet import sdk

ACTIVE = ('preparing','prepared','validating','submitting','submitted','unknown','reconciliation_required')


@dataclass(frozen=True)
class LiveLimits:
    ticket_usdc: float = 5
    max_wallet_usdc: float = 50
    max_wallet_sol_lamports: int = 30000000
    max_positions: int = 1
    max_exposure_usdc: float = 5
    daily_loss_usdc: float = 2
    daily_network_lamports: int = 5000000
    min_sol_reserve_lamports: int = 5000000
    max_network_lamports: int = 3000000
    max_priority_fee_lamports: int = 100000
    slippage_bps: int = 100
    max_price_impact_pct: float = 1
    order_max_age_seconds: int = 30

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (float,int)) or number(value) is None or value <= 0:
                raise ValueError('live limits must be positive finite numbers')
            if (name.endswith('lamports') or name in ('max_positions','slippage_bps','order_max_age_seconds')) and type(value) is not int:
                raise ValueError(name+' must be an integer')
        if money(self.ticket_usdc)*1000000 != (money(self.ticket_usdc)*1000000).to_integral_value():
            raise ValueError('live USDC ticket has more than six decimals')
        if self.slippage_bps >= 10000 or self.max_price_impact_pct > 100:
            raise ValueError('invalid live price or slippage limits')
        if self.ticket_usdc > self.max_exposure_usdc or self.max_exposure_usdc > self.max_wallet_usdc:
            raise ValueError('ticket <= exposure <= wallet cap is required')
        if self.max_priority_fee_lamports > self.max_network_lamports or self.min_sol_reserve_lamports >= self.max_wallet_sol_lamports:
            raise ValueError('invalid live fee/reserve limits')

    @property
    def version(self):
        return hashlib.sha256(encode(asdict(self)).encode()).hexdigest()


LIVE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS live_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS live_orders(id TEXT PRIMARY KEY,wallet TEXT NOT NULL,token TEXT NOT NULL,side TEXT NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,approval_hash TEXT,signature TEXT UNIQUE,data TEXT NOT NULL,error TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_intent ON live_orders(wallet) WHERE state IN ('preparing','prepared','validating','submitting','submitted','unknown','reconciliation_required');
CREATE TABLE IF NOT EXISTS live_events(id INTEGER PRIMARY KEY,order_id TEXT REFERENCES live_orders(id),at REAL NOT NULL,action TEXT NOT NULL,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS live_positions(id INTEGER PRIMARY KEY,wallet TEXT NOT NULL,token TEXT NOT NULL,quantity TEXT NOT NULL,cost_usdc TEXT NOT NULL,opened_at REAL NOT NULL,status TEXT NOT NULL,buy_order TEXT UNIQUE REFERENCES live_orders(id),sell_order TEXT UNIQUE REFERENCES live_orders(id),closed_at REAL,pnl_usdc TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_position ON live_positions(wallet,token) WHERE status='open';
CREATE TABLE IF NOT EXISTS live_settlements(order_id TEXT PRIMARY KEY REFERENCES live_orders(id),at REAL NOT NULL,pnl_usdc TEXT NOT NULL,sol_cost_lamports INTEGER NOT NULL,data TEXT NOT NULL);
'''


class LiveBook:
    def __init__(self, desk, wallet, limits=LiveLimits()):
        self.desk, self.db, self.wallet, self.limits = desk, desk.db, address(wallet), limits
        self.db.executescript(LIVE_SCHEMA)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            existing = self.db.execute("SELECT value FROM live_settings WHERE key='wallet'").fetchone()
            if existing and existing[0] != wallet:
                raise ValueError('live journal is bound to a different wallet')
            self.db.execute("INSERT OR IGNORE INTO live_settings VALUES ('wallet',?)", (wallet,))
            self.db.execute("INSERT OR IGNORE INTO live_settings VALUES ('kill_switch','on')")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def event(self, order_id, action, data):
        self.db.execute('INSERT INTO live_events(order_id,at,action,data) VALUES (?,?,?,?)',
                        (order_id,time.time(),action,encode(data)))

    def kill(self, on):
        with self.db:
            self.db.execute("UPDATE live_settings SET value=? WHERE key='kill_switch'", ('on' if on else 'off',))
            self.event(None,'kill_switch',dict(on=on))

    def positions(self):
        return self.db.execute("SELECT * FROM live_positions WHERE wallet=? AND status='open' ORDER BY id",(self.wallet,)).fetchall()

    def order(self, order_id):
        row = self.db.execute('SELECT * FROM live_orders WHERE id=? AND wallet=?',(order_id,self.wallet)).fetchone()
        if not row:
            raise ValueError('order does not exist in this wallet journal')
        return dict(row)

    def risk(self, side, token, snapshot):
        limits = self.limits
        if snapshot['wallet'] != self.wallet:
            raise ValueError('snapshot wallet mismatch')
        positions = self.positions()
        tracked = {p['token'] for p in positions}
        balances = snapshot['balances']
        if balances.get(USDC,0) > money(limits.max_wallet_usdc)*1000000 or snapshot['sol_lamports'] > limits.max_wallet_sol_lamports:
            raise ValueError('wallet exceeds isolated pilot funding cap')
        legs = 2 if side == 'buy' else 1
        if snapshot['sol_lamports'] < limits.min_sol_reserve_lamports+legs*limits.max_network_lamports:
            raise ValueError('insufficient SOL for fee budget and reserve')
        if side == 'buy':
            if self.db.execute("SELECT value FROM live_settings WHERE key='kill_switch'").fetchone()[0] != 'off':
                raise ValueError('kill switch blocks new live entries')
            if len(positions) >= limits.max_positions or token in tracked:
                raise ValueError('live position limit or duplicate token')
            if any(amount for mint,amount in balances.items() if mint != USDC and mint not in tracked):
                raise ValueError('untracked token balance requires reconciliation before buying')
            if sum((money(p['cost_usdc']) for p in positions),Decimal(0))+money(limits.ticket_usdc) > money(limits.max_exposure_usdc):
                raise ValueError('live exposure limit')
            if balances.get(USDC,0) < money(limits.ticket_usdc)*1000000:
                raise ValueError('insufficient USDC for live ticket')
            # UTC day is explicit and stable across client/host timezone changes.
            day = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            settlements = self.db.execute('SELECT pnl_usdc,sol_cost_lamports FROM live_settlements WHERE at>=?',(day,)).fetchall()
            losses = sum((max(Decimal(0),-money(row[0])) for row in settlements),Decimal(0))
            gas = sum(row[1] for row in settlements)
            if losses >= money(limits.daily_loss_usdc) or gas+limits.max_network_lamports > limits.daily_network_lamports:
                raise ValueError('daily realized loss or SOL cost budget blocks new entries')
        elif side == 'sell':
            position = next((p for p in positions if p['token'] == token),None)
            if not position or balances.get(token,0) != uint(position['quantity']):
                raise ValueError('sell requires exact reconciliation to an open live position')
        else:
            raise ValueError('side must be buy or sell')

    def reserve(self, token, side):
        address(token)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT id FROM live_orders WHERE state IN (?,?,?,?,?,?,?)',ACTIVE).fetchone():
                raise ValueError('an active or unresolved order blocks another order; recover or cancel it first')
            order_id, now = uuid.uuid4().hex,time.time()
            self.db.execute('INSERT INTO live_orders(id,wallet,token,side,state,created_at,updated_at,data) VALUES (?,?,?,?,?,?,?,?)',
                            (order_id,self.wallet,token,side,'preparing',now,now,'{}'))
            self.event(order_id,'reserved',dict(token=token,side=side))
            self.db.commit()
            return order_id
        except Exception:
            self.db.rollback()
            raise

    def cancel(self, order_id):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            order = self.order(order_id)
            if order['state'] not in ('preparing','prepared','validating') or order['signature']:
                raise ValueError('broadcast or uncertain orders cannot be cancelled locally')
            self.db.execute("UPDATE live_orders SET state='cancelled',updated_at=? WHERE id=?",(time.time(),order_id))
            self.event(order_id,'cancelled',{})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def status(self):
        orders = self.db.execute('SELECT id,token,side,state,signature,created_at,updated_at,error FROM live_orders ORDER BY created_at DESC').fetchall()
        return dict(wallet=self.wallet,kill_switch=self.db.execute("SELECT value FROM live_settings WHERE key='kill_switch'").fetchone()[0],
                    limits=asdict(self.limits),positions=[dict(p) for p in self.positions()],orders=[dict(o) for o in orders])


class LiveEngine:
    def __init__(self, book, signer, rpc, jupiter, scanner, token_rpc, quote_client):
        self.book, self.signer, self.rpc, self.jupiter = book, signer, rpc, jupiter
        self.scanner, self.token_rpc, self.quote_client = scanner, token_rpc, quote_client
        self.wallet = book.wallet
        if str(signer.pubkey()) != self.wallet:
            raise ValueError('signer differs from journal wallet')

    def _native_mint(self):
        result,stamp = self.token_rpc.call('getAccountInfo',[WRAPPED_SOL,{'encoding':'jsonParsed','commitment':'confirmed'}])
        account = result['value']
        info = parsed_account(account,'mint',TOKEN_PROGRAM)
        if (info.get('isInitialized') is not True or uint(info['decimals']) != 9 or uint(info['supply']) != 0
                or 'mintAuthority' not in info or info['mintAuthority'] is not None
                or 'freezeAuthority' not in info or info['freezeAuthority'] is not None
                or extensions(account,'mint',WRAPPED_SOL)):
            raise ValueError('canonical wrapped SOL mint did not match its expected immutable native structure')
        return dict(token=WRAPPED_SOL,source='solana_rpc',program=TOKEN_PROGRAM,decimals=9,supply='0',
                    mint_authority=None,freeze_authority=None,supported_features=True,
                    observed_at=stamp,slot=uint(result['context']['slot']),native_wrapped_sol=True)

    def prepare(self, token, side, execution_test=False):
        if execution_test and token != WRAPPED_SOL:
            raise ValueError('execution test is restricted to canonical wrapped SOL')
        order_id = self.book.reserve(token,side)
        try:
            self.rpc.mainnet()
            rules = replace(self.book.desk.rules,ticket=self.book.limits.ticket_usdc,require_preflight=True)
            # Exits require a supported mint, but concentration and market-entry
            # rules cannot trap an already-open position.
            if execution_test:
                mint = self._native_mint()
                quotes = self.quote_client.roundtrip(token,rules.ticket,rules.max_quote_slippage_bps)
                now = time.time()
                reasons = quote_reasons(token,quotes,rules,now)
                evidence = dict(token=token,mint=mint,holders=None,quotes=quotes,observed_at=now,
                                errors=[],reasons=reasons,screen_passed=not reasons,ready_for_live=False,
                                mode='execution_test_only')
                self.book.desk.preflight(self.book.desk.run('live_execution_test',now),evidence)
                if reasons:
                    raise ValueError('execution test quote rejected: '+','.join(reasons))
                s = None
            elif side == 'buy':
                evidence = collect(token,rules,self.token_rpc,self.quote_client)
                self.book.desk.preflight(self.book.desk.run('live_preflight',time.time()),evidence)
                s = best_pair(self.scanner.pairs(token))
                if s is None:
                    raise ValueError('no supported market pair')
                s.update(holders=evidence['holders'],preflight=evidence)
                reasons = vet(s,rules,time.time())
                if reasons:
                    raise ValueError('live candidate rejected: '+','.join(reasons))
                mint = evidence['mint']
            else:
                # Fetch just the mint for structural account-program identity.
                from .onchain import mint_evidence
                result,stamp = self.token_rpc.call('getAccountInfo',[token,{'encoding':'jsonParsed','commitment':'confirmed'}])
                # A position opened by the explicit native-token execution test
                # can be closed without reusing its zero-supply mint as a memecoin.
                position = next((p for p in self.book.positions() if p['token'] == token),None)
                parent = json.loads(self.book.order(position['buy_order'])['data']) if position and position['buy_order'] else {}
                if token == WRAPPED_SOL and parent.get('execution_test'):
                    mint = self._native_mint()
                else:
                    mint = mint_evidence(token,result,stamp)
                evidence, s = None,None
            token_ata = ata(self.wallet,token,mint['program'])
            usdc_ata = ata(self.wallet,USDC,TOKEN_PROGRAM)
            input_ata,output_ata = (usdc_ata,token_ata) if side == 'buy' else (token_ata,usdc_ata)
            before = self.rpc.snapshot(self.wallet,(input_ata,output_ata))
            self.book.risk(side,token,before)
            input_mint,output_mint = (USDC,token) if side == 'buy' else (token,USDC)
            amount = int(money(self.book.limits.ticket_usdc)*1000000) if side == 'buy' else before['balances'][token]
            source = token_account(before['accounts'][input_ata],self.wallet,input_mint)
            if not source or source['amount'] < amount:
                raise ValueError('input funds must be in the wallet associated token account')
            built = self.jupiter.build(self.wallet,input_mint,output_mint,amount,self.book.limits)
            tx,inspection = inspect(built['unsigned_transaction'],self.wallet,input_ata,output_ata,
                                   input_mint,output_mint,built['quote'],self.book.limits,self.rpc)
            _,_,_,message_bytes = sdk()
            fee = self.rpc.call('getFeeForMessage',[base64.b64encode(message_bytes(tx.message)).decode(),{'commitment':'confirmed'}])
            if fee['value'] is None or uint(fee['value']) > self.book.limits.max_network_lamports:
                raise ValueError('transaction fee unavailable, expired or above budget')
            simulation = self.rpc.simulate(built['unsigned_transaction'],list(before['accounts']),min_slot=before['slot'])
            effects = simulation_effects(before,simulation,input_ata,output_ata,built['quote'],self.book.limits)
            now = time.time()
            if now-built['observed_at'] > self.book.limits.order_max_age_seconds:
                raise ValueError('order expired during preparation; prepare a fresh order')
            data = dict(order_id=order_id,wallet=self.wallet,token=token,side=side,
                        execution_test=execution_test,
                        limits=asdict(self.book.limits),limits_version=self.book.limits.version,
                        rules_version=rules.version,entry_snapshot=s,preflight=evidence,mint=mint,
                        input_ata=input_ata,output_ata=output_ata,before=before,built=built,
                        inspection=inspection,simulation_effects=effects,estimated_transaction_fee_lamports=uint(fee['value']))
            approval = hashlib.sha256(encode(data).encode()).hexdigest()
            with self.book.db:
                cursor = self.book.db.execute("UPDATE live_orders SET state='prepared',data=?,approval_hash=?,updated_at=? WHERE id=? AND state='preparing'",
                    (encode(data),approval,now,order_id))
                if cursor.rowcount != 1:
                    raise ValueError('order reservation was cancelled while preparing')
                self.book.event(order_id,'prepared',dict(approval_hash=approval,message_hash=inspection['message_hash']))
            return self.review(order_id)
        except Exception as error:
            with self.book.db:
                self.book.db.execute("UPDATE live_orders SET state='failed',error=?,updated_at=? WHERE id=? AND state='preparing'",
                                     (str(error),time.time(),order_id))
                self.book.event(order_id,'prepare_failed',dict(error=str(error)))
            raise

    def review(self, order_id):
        order = self.book.order(order_id)
        data = json.loads(order['data'])
        built = data.get('built') or {}
        quote = built.get('quote') or {}
        return dict(order_id=order_id,state=order['state'],wallet=self.wallet,token=order['token'],side=order['side'],
                    execution_test=data.get('execution_test',False),
                    input_mint=quote.get('inputMint'),input_amount=quote.get('inAmount'),
                    output_mint=quote.get('outputMint'),minimum_output=quote.get('otherAmountThreshold'),
                    expires_at=built.get('observed_at',0)+self.book.limits.order_max_age_seconds,
                    simulation_effects=data.get('simulation_effects'),limits=data.get('limits'),
                    inspection=data.get('inspection'),approval_hash=order['approval_hash'],
                    submitted=order['signature'] is not None)

    def submit(self, order_id, approval):
        # Claim the order before network I/O. Provider audit writes must never
        # commit an in-progress submission transaction through the shared DB.
        self.book.db.execute('BEGIN IMMEDIATE')
        try:
            order = self.book.order(order_id)
            data = json.loads(order['data'])
            if order['state'] != 'prepared' or order['signature']:
                raise ValueError('only a prepared, never-broadcast order can be submitted')
            if (approval != order['approval_hash'] or hashlib.sha256(encode(data).encode()).hexdigest() != approval
                    or data['limits_version'] != self.book.limits.version):
                raise ValueError('approval hash or current limits do not match reviewed order')
            self.book.db.execute("UPDATE live_orders SET state='validating',updated_at=? WHERE id=?",(time.time(),order_id))
            self.book.event(order_id,'validation_claimed',{})
            self.book.db.commit()
        except Exception:
            self.book.db.rollback()
            raise
        try:
            now = time.time()
            if not 0 <= now-data['built']['observed_at'] <= self.book.limits.order_max_age_seconds:
                raise ValueError('reviewed order is stale; cancel and prepare another')
            rules = replace(self.book.desk.rules,ticket=self.book.limits.ticket_usdc,require_preflight=True)
            if data['rules_version'] != rules.version:
                raise ValueError('entry rules changed since preparation')
            if order['side'] == 'buy' and not data.get('execution_test'):
                reasons = vet(data['entry_snapshot'],rules,now)
                if reasons:
                    raise ValueError('entry evidence became ineligible: '+','.join(reasons))
            elif data.get('execution_test'):
                if order['token'] != WRAPPED_SOL:
                    raise ValueError('execution-test token differs from canonical wrapped SOL')
                reasons = quote_reasons(WRAPPED_SOL,data['preflight']['quotes'],rules,now)
                if reasons:
                    raise ValueError('execution test quote evidence became stale or ineligible')
            self.rpc.mainnet()
            if uint(self.rpc.call('getBlockHeight',[{'commitment':'confirmed'}])) > data['built']['last_valid_block_height']:
                raise ValueError('transaction blockhash has expired')
            current = self.rpc.snapshot(self.wallet,tuple(data['before']['accounts']))
            if current['accounts'] != data['before']['accounts']:
                raise ValueError('wallet accounts changed since reviewed simulation; prepare another order')
            self.book.risk(order['side'],order['token'],current)
            tx,inspection = inspect(data['built']['unsigned_transaction'],self.wallet,data['input_ata'],data['output_ata'],
                data['built']['quote']['inputMint'],data['built']['quote']['outputMint'],data['built']['quote'],self.book.limits,self.rpc)
            if inspection['message_hash'] != data['inspection']['message_hash']:
                raise ValueError('reviewed transaction message changed')
            _,_,VersionedTransaction,_ = sdk()
            signed = VersionedTransaction(tx.message,[self.signer])
            signed.verify_and_hash_message()
            signed_b64 = base64.b64encode(bytes(signed)).decode()
            simulation = self.rpc.simulate(signed_b64,list(current['accounts']),verify=True,min_slot=current['slot'])
            simulation_effects(current,simulation,data['input_ata'],data['output_ata'],data['built']['quote'],self.book.limits)
            signature = str(signed.signatures[0])
            self.book.db.execute('BEGIN IMMEDIATE')
            try:
                self.book.risk(order['side'],order['token'],current)
                if time.time()-data['built']['observed_at'] > self.book.limits.order_max_age_seconds:
                    raise ValueError('order expired before submission')
                cursor = self.book.db.execute("UPDATE live_orders SET state='submitting',signature=?,updated_at=? WHERE id=? AND state='validating' AND signature IS NULL",
                                              (signature,time.time(),order_id))
                if cursor.rowcount != 1:
                    raise ValueError('order was cancelled during validation')
                self.book.event(order_id,'submission_intent',dict(signature=signature))
                # Commit BEFORE sending; a crash can never hide a potentially broadcast order.
                self.book.db.commit()
            except Exception:
                self.book.db.rollback()
                raise
        except Exception:
            with self.book.db:
                self.book.db.execute("UPDATE live_orders SET state='prepared',updated_at=? WHERE id=? AND state='validating'",(time.time(),order_id))
            raise
        try:
            response = self.rpc.send(signed_b64)
            if response != signature:
                raise ValueError('RPC signature differs from signed transaction')
            state,error = 'submitted',None
        except Exception:
            state,error = 'unknown','submission outcome uncertain; recover signature before any new order'
        with self.book.db:
            self.book.db.execute("UPDATE live_orders SET state=?,error=?,updated_at=? WHERE id=? AND state='submitting'",
                                (state,error,time.time(),order_id))
            self.book.event(order_id,state,dict(signature=signature))
        return dict(order_id=order_id,state=state,signature=signature,confirmed=False)

    def recover(self, order_id):
        order = self.book.order(order_id)
        if order['state'] in ('confirmed','failed','expired','cancelled'):
            return dict(order_id=order_id,state=order['state'],signature=order['signature'])
        if not order['signature']:
            return dict(order_id=order_id,state=order['state'],action='cancel never-broadcast intent or finish preparation')
        data = json.loads(order['data'])
        self.rpc.mainnet()
        statuses = self.rpc.call('getSignatureStatuses',[[order['signature']],{'searchTransactionHistory':True}])
        if not isinstance(statuses.get('value'),list) or len(statuses['value']) != 1:
            raise ValueError('invalid signature status response')
        status = statuses['value'][0]
        # Finalized transaction metadata, rather than a send acknowledgement,
        # is the authority for all position and P&L accounting.
        chain = self.rpc.call('getTransaction',[order['signature'],
                             {'encoding':'base64','commitment':'finalized','maxSupportedTransactionVersion':0}])
        if chain is None:
            height = uint(self.rpc.call('getBlockHeight',[{'commitment':'finalized'}]))
            valid = self.rpc.call('isBlockhashValid',[data['inspection']['blockhash'],{'commitment':'finalized'}])
            if status is None and valid.get('value') is False and height > data['built']['last_valid_block_height']+32:
                current = self.rpc.snapshot(self.wallet,tuple(data['before']['accounts']))
                if current['accounts'] == data['before']['accounts']:
                    with self.book.db:
                        self.book.db.execute("UPDATE live_orders SET state='expired',updated_at=? WHERE id=? AND state NOT IN ('confirmed','failed','cancelled')",(time.time(),order_id))
                        self.book.event(order_id,'expiry_proved',dict(finalized_height=height,balances_unchanged=True))
                    return dict(order_id=order_id,state='expired',signature=order['signature'])
                return self._blocked(order_id,'expired transaction has unexplained wallet changes')
            return dict(order_id=order_id,state=order['state'],signature=order['signature'],action='wait for finalized transaction metadata')
        try:
            effects = self._chain_effects(order,data,chain)
            current = self.rpc.snapshot(self.wallet,tuple(data['before']['accounts']))
            before = data['before']
            expected = dict(before['balances'])
            quote = data['built']['quote']
            if effects['success']:
                expected[quote['inputMint']] = expected.get(quote['inputMint'],0)-uint(effects['input_spent'])
                expected[quote['outputMint']] = expected.get(quote['outputMint'],0)+uint(effects['output_received'])
            clean = lambda values:{key:value for key,value in values.items() if value}
            if (clean(current['balances']) != clean(expected)
                    or current['sol_lamports'] != effects['post_wallet_lamports']):
                raise ValueError('current wallet balances do not reconcile with finalized transaction')
            self.book.db.execute('BEGIN IMMEDIATE')
            try:
                fresh = self.book.order(order_id)
                if fresh['state'] in ('confirmed','failed'):
                    self.book.db.rollback()
                    return dict(order_id=order_id,state=fresh['state'],signature=order['signature'])
                pnl = Decimal(0)
                now = time.time()
                state = 'confirmed' if effects['success'] else 'failed'
                if effects['success'] and order['side'] == 'buy':
                    self.book.db.execute("INSERT INTO live_positions(wallet,token,quantity,cost_usdc,opened_at,status,buy_order) VALUES (?,?,?,?,?,'open',?)",
                        (self.wallet,order['token'],effects['output_received'],str(money(effects['input_spent'])/1000000),now,order_id))
                elif effects['success']:
                    position = next((p for p in self.book.positions() if p['token'] == order['token']),None)
                    if not position or uint(position['quantity']) != uint(effects['input_spent']):
                        raise ValueError('finalized sell does not match journal position')
                    pnl = money(effects['output_received'])/1000000-money(position['cost_usdc'])
                    self.book.db.execute("UPDATE live_positions SET status='closed',sell_order=?,closed_at=?,pnl_usdc=? WHERE id=?",
                                         (order_id,now,str(pnl),position['id']))
                settled_at = number(chain.get('blockTime')) or now
                self.book.db.execute('INSERT INTO live_settlements(order_id,at,pnl_usdc,sol_cost_lamports,data) VALUES (?,?,?,?,?)',
                                     (order_id,settled_at,str(pnl),effects['sol_cost_lamports'],encode(effects)))
                self.book.db.execute('UPDATE live_orders SET state=?,updated_at=?,error=? WHERE id=?',
                                     (state,now,None if effects['success'] else 'finalized transaction failed',order_id))
                self.book.event(order_id,'reconciled',dict(state=state,**effects))
                self.book.db.commit()
            except Exception:
                self.book.db.rollback()
                raise
            return dict(order_id=order_id,state=state,signature=order['signature'],effects=effects,pnl_usdc=str(pnl))
        except (ValueError,KeyError,TypeError) as error:
            return self._blocked(order_id,str(error))

    def _blocked(self, order_id, error):
        with self.book.db:
            self.book.db.execute("UPDATE live_orders SET state='reconciliation_required',error=?,updated_at=? WHERE id=? AND state NOT IN ('confirmed','failed','expired','cancelled')",
                                 (error,time.time(),order_id))
            self.book.db.execute("UPDATE live_settings SET value='on' WHERE key='kill_switch'")
            self.book.event(order_id,'reconciliation_blocked',dict(error=error))
        return dict(order_id=order_id,state='reconciliation_required',error=error)

    def _chain_effects(self, order, data, chain):
        limits = LiveLimits(**data['limits'])
        if not isinstance(chain.get('transaction'),list) or chain['transaction'][1] != 'base64':
            raise ValueError('canonical transaction lacks base64 bytes')
        tx = decode(chain['transaction'][0])
        tx.verify_and_hash_message()
        _,_,_,message_bytes = sdk()
        if (str(tx.signatures[0]) != order['signature']
                or hashlib.sha256(message_bytes(tx.message)).hexdigest() != data['inspection']['message_hash']):
            raise ValueError('canonical transaction differs from submitted signature or message')
        meta = chain['meta']
        loaded = meta.get('loadedAddresses') or {'writable':[],'readonly':[]}
        keys = [str(key) for key in tx.message.account_keys]+loaded['writable']+loaded['readonly']
        if keys != data['inspection']['account_keys']:
            raise ValueError('canonical loaded address table differs from inspected message')
        if len(meta['preBalances']) != len(keys) or len(meta['postBalances']) != len(keys):
            raise ValueError('canonical balance vectors are incomplete')
        before_sol, after_sol = uint(meta['preBalances'][0]),uint(meta['postBalances'][0])
        sol_cost = before_sol-after_sol
        if (before_sol != data['before']['sol_lamports'] or sol_cost < 0
                or sol_cost > limits.max_network_lamports or uint(meta['fee']) > sol_cost):
            raise ValueError('canonical SOL costs differ from reviewed balance or limits')
        success = meta.get('err') is None
        quote = data['built']['quote']
        def balance(rows, account, mint):
            index = keys.index(account)
            matches = [row for row in rows if row['accountIndex'] == index]
            if not matches:
                return 0
            if len(matches) != 1 or matches[0].get('owner') != self.wallet or matches[0]['mint'] != mint:
                raise ValueError('canonical token balance identity mismatch')
            return uint(matches[0]['uiTokenAmount']['amount'])
        old_input = token_account(data['before']['accounts'][data['input_ata']],self.wallet,quote['inputMint'])
        old_output = token_account(data['before']['accounts'][data['output_ata']],self.wallet,quote['outputMint'])
        pre_in = balance(meta.get('preTokenBalances') or [],data['input_ata'],quote['inputMint'])
        pre_out = balance(meta.get('preTokenBalances') or [],data['output_ata'],quote['outputMint'])
        if pre_in != (old_input['amount'] if old_input else 0) or pre_out != (old_output['amount'] if old_output else 0):
            raise ValueError('canonical token starting balances differ from reviewed snapshot')
        spent = pre_in-balance(meta.get('postTokenBalances') or [],data['input_ata'],quote['inputMint'])
        received = balance(meta.get('postTokenBalances') or [],data['output_ata'],quote['outputMint'])-pre_out
        if success:
            if spent != uint(quote['inAmount']) or received < uint(quote['otherAmountThreshold']):
                raise ValueError('finalized swap violates approved amounts')
            if after_sol < limits.min_sol_reserve_lamports:
                raise ValueError('finalized wallet fell below SOL reserve')
        elif spent != 0 or received != 0:
            raise ValueError('failed transaction has unexplained token changes')
        return dict(success=success,input_spent=str(spent),output_received=str(received),
                    sol_cost_lamports=sol_cost,transaction_fee_lamports=uint(meta['fee']),post_wallet_lamports=after_sol)
