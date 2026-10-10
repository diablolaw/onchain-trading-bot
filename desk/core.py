from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def money(value):
    return Decimal(str(value))


def usd(value):
    """Settle simulated USD cash to eight decimals, with Decimal half-even rounding."""
    return money(value).quantize(Decimal('0.00000001'))


@dataclass(frozen=True)
class Rules:
    initial_cash: float = 1000
    ticket: float = 25
    max_positions: int = 5
    max_exposure: float = 125
    min_liquidity: float = 50000
    min_age_hours: float = 6
    max_age_hours: float = 720
    min_volume_24h: float = 10000
    min_market_cap: float = 0
    max_market_cap: float = 0
    min_trades_24h: int = 0
    min_holders: int = 0
    concentration_measure: str = 'owner_aggregated'
    reject_no_sells: bool = False
    max_top_owner_pct: float = 10
    max_top10_owner_pct: float = 35
    max_snapshot_age: int = 120
    max_holder_age: int = 3600
    fee_bps: float = 30
    slippage_bps: float = 100
    fixed_fee_usd: float = 0.05
    minimum_fee_usd: float = 0
    exit_policy: str = 'pilot'
    max_ticket_liquidity_pct: float = 0.1
    stop_loss_pct: float = 15
    take_profit_pct: float = 30
    max_hold_seconds: int = 86400
    volume_exit_ratio: float = 0.2
    cooldown_seconds: int = 3600
    require_preflight: bool = False
    max_preflight_age: int = 30
    max_price_impact_pct: float = 1
    max_quote_slippage_bps: int = 100
    max_quote_fee_bps: float = 50
    max_roundtrip_loss_pct: float = 3
    max_quote_network_lamports: int = 5000000

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name=='exit_policy':
                if value not in ('pilot','post_volume'):raise ValueError('invalid exit policy')
                continue
            if name=='concentration_measure':
                if value not in ('owner_aggregated','token_accounts'): raise ValueError('invalid concentration measure')
                continue
            if name in ('require_preflight','reject_no_sells'):
                if not isinstance(value, bool):
                    raise ValueError(name+' must be boolean')
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or number(value) is None:
                raise ValueError(f'{name} must be finite and nonnegative')
        for name in ('max_positions', 'max_snapshot_age', 'max_holder_age', 'max_hold_seconds', 'cooldown_seconds',
                     'max_preflight_age', 'max_quote_slippage_bps', 'max_quote_network_lamports', 'min_trades_24h', 'min_holders'):
            if not isinstance(getattr(self, name), int) or isinstance(getattr(self, name), bool):
                raise ValueError(f'{name} must be an integer')
        if usd(self.ticket) <= 0 or usd(self.initial_cash) <= 0 or self.max_positions < 1:
            raise ValueError('cash, ticket and position count must be positive')
        if self.min_age_hours > self.max_age_hours or self.slippage_bps >= 10000 or self.fee_bps >= 10000:
            raise ValueError('invalid age range or execution costs')
        if self.max_market_cap and self.min_market_cap > self.max_market_cap:
            raise ValueError('invalid market cap range')
        for name in ('max_top_owner_pct', 'max_top10_owner_pct', 'stop_loss_pct', 'max_ticket_liquidity_pct'):
            if getattr(self, name) > 100:
                raise ValueError(f'{name} must be <= 100')
        if self.max_quote_slippage_bps > 10000 or self.max_quote_fee_bps > 10000:
            raise ValueError('quote slippage and fee limits must be <= 10000 bps')

    @property
    def version(self):
        return hashlib.sha256(encode(asdict(self)).encode()).hexdigest()[:16]


def signed_number(value):
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (ValueError,TypeError,OverflowError):return None


def normalize(pair, observed_at, holders=None):
    base = pair.get('baseToken') or {}
    liquidity = pair.get('liquidity') or {}
    volume = pair.get('volume') or {}
    created = number(pair.get('pairCreatedAt'))
    return dict(chain=pair.get('chainId'), token=base.get('address'),
                symbol=base.get('symbol', '?'), pair=pair.get('pairAddress'),
                observed_at=observed_at, price=number(pair.get('priceUsd')),
                liquidity=number(liquidity.get('usd')), volume_6h=number(volume.get('h6')),
                volume_24h=number(volume.get('h24')), created_at=created / 1000 if created is not None else None,
                holders=holders, buys_1h=number(((pair.get('txns') or {}).get('h1') or {}).get('buys')),
                sells_1h=number(((pair.get('txns') or {}).get('h1') or {}).get('sells')),
                market_cap=number(pair.get('marketCap')), fdv=number(pair.get('fdv')), dex_id=pair.get('dexId'),
                volume_windows={k:number(volume.get(k)) for k in ('m5','h1','h6','h24')},
                price_change_windows={k:signed_number((pair.get('priceChange') or {}).get(k)) for k in ('m5','h1','h6','h24')},
                trade_windows={k:{side:number(((pair.get('txns') or {}).get(k) or {}).get(side)) for side in ('buys','sells')} for k in ('m5','h1','h6','h24')},
                buys_24h=number(((pair.get('txns') or {}).get('h24') or {}).get('buys')),
                sells_24h=number(((pair.get('txns') or {}).get('h24') or {}).get('sells')))


def screening_market(snapshot):
    """Venue free-cut metrics; preserve pair measurements for risk and execution."""
    venue=snapshot.get('fomo')
    if venue is None: return snapshot
    if not isinstance(venue,dict) or venue.get('token')!=snapshot.get('token'):
        return dict(snapshot,liquidity=None,volume_24h=None,market_cap=None,created_at=None)
    return dict(snapshot,**{field:venue.get(field) for field in ('liquidity','volume_24h','market_cap','created_at')})


def concentration(holders,rules):
    fields=('top_account_pct','top10_account_pct') if rules.concentration_measure=='token_accounts' else ('top_owner_pct','top10_owner_pct')
    return tuple(number(holders.get(field)) for field in fields)


def vet(snapshot, rules, now, *, check_quotes=True):
    """Pure, ordered rules. Unknown or invalid evidence rejects entry."""
    reasons = []
    s = snapshot
    if s.get('chain') != 'solana' or not s.get('token') or not s.get('pair'):
        reasons.append('invalid_identity')
    age = now - s['observed_at']
    if age < 0 or age > rules.max_snapshot_age:
        reasons.append('stale_market_data')
    if number(s.get('price')) is None or s['price'] <= 0:
        reasons.append('missing_price')
    market=screening_market(s)
    if number(market.get('liquidity')) is None or market['liquidity'] < rules.min_liquidity:
        reasons.append('low_or_missing_liquidity')
    created = number(market.get('created_at'))
    if created is None or not rules.min_age_hours <= (now - created) / 3600 <= rules.max_age_hours:
        reasons.append('pair_age_out_of_range')
    if number(market.get('volume_24h')) is None or market['volume_24h'] < rules.min_volume_24h:
        reasons.append('low_or_missing_volume')
    from .filters import additional_checks
    reasons.extend(c['reason'] for c in additional_checks(market,rules) if c['status'] in ('fail','missing'))
    if s.get('fomo') is not None:
        venue=s.get('fomo') or {}
        stamp=number(venue.get('observed_at'))
        if venue.get('token')!=s.get('token') or stamp is None or not 0<=now-stamp<=rules.max_snapshot_age:
            reasons.append('fomo_evidence_stale_or_unbound')
        if number(s.get('liquidity')) is None or s['liquidity']<rules.min_liquidity:
            reasons.append('execution_pair_liquidity_low_or_missing')
    holders = s.get('holders') or {}
    stamp = number(holders.get('observed_at'))
    if (holders.get('token') != s.get('token') or holders.get('method') != rules.concentration_measure
            or not holders.get('source') or stamp is None or not 0 <= now - stamp <= rules.max_holder_age):
        reasons.append('missing_or_stale_owner_evidence')
    top,top10 = concentration(holders,rules)
    if top is None or top10 is None or not top <= top10 <= 100:
        reasons.append('invalid_owner_concentration')
    elif top > rules.max_top_owner_pct or top10 > rules.max_top10_owner_pct:
        reasons.append('owner_concentration')
    if rules.require_preflight:
        from .preflight import preflight_reasons
        reasons.extend(preflight_reasons(s.get('token'), s.get('preflight'), rules, now, check_quotes=check_quotes))
    return list(dict.fromkeys(reasons))


SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started_at REAL NOT NULL, kind TEXT NOT NULL, rules_version TEXT NOT NULL, rules_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS raw_events(id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), observed_at REAL NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), token TEXT NOT NULL, pair TEXT NOT NULL, observed_at REAL NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS snapshots_pair_time ON snapshots(pair, observed_at);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), snapshot_id INTEGER REFERENCES snapshots(id), at REAL NOT NULL, role TEXT NOT NULL, action TEXT NOT NULL, reasons TEXT NOT NULL, rules_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1), cash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preflights(id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), token TEXT NOT NULL, observed_at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY, token TEXT NOT NULL, pair TEXT NOT NULL, symbol TEXT NOT NULL, opened_at REAL NOT NULL, quantity TEXT NOT NULL, entry_price TEXT NOT NULL, cost TEXT NOT NULL, status TEXT NOT NULL, closed_at REAL, proceeds TEXT, pnl TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_token ON positions(token) WHERE status='open';
CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY, position_id INTEGER REFERENCES positions(id), snapshot_id INTEGER REFERENCES snapshots(id), side TEXT NOT NULL, at REAL NOT NULL, price TEXT NOT NULL, quantity TEXT NOT NULL, fee TEXT NOT NULL, cash_after TEXT NOT NULL);
'''


class Desk:
    def __init__(self, path, rules=Rules()):
        self.rules = rules
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)
        self.db.execute('CREATE TABLE IF NOT EXISTS risk_close_intents(pair TEXT PRIMARY KEY,requested_at REAL NOT NULL,reason TEXT NOT NULL)')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('schema_version', '1'))
            self.db.execute('INSERT OR IGNORE INTO account VALUES (1,?)', (str(usd(rules.initial_cash)),))

    def close(self):
        self.db.close()

    def run(self, kind, now):
        with self.db:
            return self.db.execute('INSERT INTO runs(started_at,kind,rules_version,rules_json) VALUES (?,?,?,?)',
                                   (now, kind, self.rules.version, encode(asdict(self.rules)))).lastrowid

    def raw(self, run_id, source, payload, now):
        with self.db:
            self.db.execute('INSERT INTO raw_events(run_id,observed_at,source,payload) VALUES (?,?,?,?)',
                            (run_id, now, source, encode(payload)))

    def preflight(self, run_id, evidence):
        with self.db:
            pid = self.db.execute('INSERT INTO preflights(run_id,token,observed_at,data) VALUES (?,?,?,?)',
                                 (run_id, evidence['token'], evidence['observed_at'], encode(evidence))).lastrowid
            self.log(run_id, None, evidence['observed_at'], 'PREFLIGHT',
                     'screen_pass' if evidence['screen_passed'] else 'reject',
                     evidence['reasons'] + ['preflight_id:' + str(pid)])
            return pid

    def log(self, run_id, snapshot_id, now, role, action, reasons):
        self.db.execute('INSERT INTO decisions(run_id,snapshot_id,at,role,action,reasons,rules_version) VALUES (?,?,?,?,?,?,?)',
                        (run_id, snapshot_id, now, role, action, encode(reasons), self.rules.version))

    def open_positions(self):
        return self.db.execute("SELECT * FROM positions WHERE status='open' ORDER BY id").fetchall()

    def record(self, run_id, snapshot, now, enter=True):
        # A single write lock makes fill, account and decision updates atomic across workers.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            sid = self.db.execute('INSERT INTO snapshots(run_id,token,pair,observed_at,data) VALUES (?,?,?,?,?)',
                                  (run_id, snapshot['token'], snapshot['pair'], snapshot['observed_at'], encode(snapshot))).lastrowid
            latest = self.db.execute('SELECT MAX(observed_at) FROM snapshots WHERE pair=? AND id<>?', (snapshot['pair'], sid)).fetchone()[0]
            if latest is not None and snapshot['observed_at'] <= latest:
                self.log(run_id, sid, now, 'RISK', 'skip', ['duplicate_or_out_of_order_snapshot'])
            else:
                self._exit(run_id, sid, snapshot, now)
                if enter:
                    reasons = vet(snapshot, self.rules, now)
                    self.log(run_id, sid, now, 'VET', 'reject' if reasons else 'approve', reasons)
                    if not reasons:
                        self._entry(run_id, sid, snapshot, now)
            self.db.commit()
            return sid
        except Exception:
            self.db.rollback()
            raise

    def _entry(self, run_id, sid, s, now):
        r = self.rules
        positions = self.open_positions()
        reasons = []
        if any(p['token'] == s['token'] for p in positions):
            reasons.append('already_open')
        last = self.db.execute('SELECT MAX(closed_at) FROM positions WHERE token=?', (s['token'],)).fetchone()[0]
        if last is not None and now - last < r.cooldown_seconds:
            reasons.append('cooldown')
        if len(positions) >= r.max_positions:
            reasons.append('position_limit')
        cost = usd(r.ticket)
        fee = usd(max(money(r.minimum_fee_usd),cost * money(r.fee_bps) / 10000 + money(r.fixed_fee_usd)))
        cash = money(self.db.execute('SELECT cash FROM account').fetchone()[0])
        if cash < cost + fee:
            reasons.append('insufficient_cash')
        if sum((money(p['cost']) for p in positions), Decimal(0)) + cost + fee > money(r.max_exposure):
            reasons.append('exposure_limit')
        if r.ticket > s['liquidity'] * r.max_ticket_liquidity_pct / 100:
            reasons.append('liquidity_size_limit')
        if reasons:
            self.log(run_id, sid, now, 'SIZE', 'skip', reasons)
            return
        price = money(s['price']) * (1 + money(r.slippage_bps) / 10000)
        quantity = cost / price
        pid = self.db.execute("INSERT INTO positions(token,pair,symbol,opened_at,quantity,entry_price,cost,status) VALUES (?,?,?,?,?,?,?,'open')",
                              (s['token'], s['pair'], s['symbol'], now, str(quantity), str(price), str(cost + fee))).lastrowid
        self._fill(pid, sid, 'buy', now, price, quantity, fee, cash - cost - fee)
        self.log(run_id, sid, now, 'FILLS', 'buy', ['eligible_fixed_ticket'])

    def _fill(self, pid, sid, side, now, price, quantity, fee, cash):
        self.db.execute('UPDATE account SET cash=? WHERE id=1', (str(cash),))
        self.db.execute('INSERT INTO fills(position_id,snapshot_id,side,at,price,quantity,fee,cash_after) VALUES (?,?,?,?,?,?,?,?)',
                        (pid, sid, side, now, str(price), str(quantity), str(fee), str(cash)))

    def _exit(self, run_id, sid, s, now):
        r = self.rules
        p = self.db.execute("SELECT * FROM positions WHERE pair=? AND status='open'", (s['pair'],)).fetchone()
        if not p:
            return
        if (number(s.get('price')) is None or s['price'] <= 0
                or not 0 <= now - s['observed_at'] <= r.max_snapshot_age):
            self.log(run_id, sid, now, 'RISK', 'blocked', ['missing_or_stale_exit_price'])
            return
        change = (money(s['price']) / money(p['entry_price']) - 1) * 100
        reason = None
        pending=self.db.execute('SELECT reason FROM risk_close_intents WHERE pair=?',(s['pair'],)).fetchone()
        # A pending close waits for an actual fresh price; never fabricate a fill.
        if r.exit_policy=='post_volume' and pending:
            reason=pending[0]
        elif r.exit_policy=='pilot' and change <= -money(r.stop_loss_pct):
            reason = 'stop_loss'
        elif r.exit_policy=='pilot' and number(s.get('liquidity')) is not None and s['liquidity'] < r.min_liquidity:
            reason = 'liquidity_deterioration'
        elif r.exit_policy=='pilot' and change >= money(r.take_profit_pct):
            reason = 'take_profit'
        elif r.exit_policy=='pilot' and now - p['opened_at'] >= r.max_hold_seconds:
            reason = 'max_hold'
        elif number(s.get('volume_24h')) is not None and s['volume_24h'] > 0 and number(s.get('volume_6h')) is not None:
            if s['volume_6h'] / (s['volume_24h'] / 4) < r.volume_exit_ratio:
                reason = 'volume_decay'
        if reason is None:
            self.log(run_id, sid, now, 'RISK', 'hold', [])
            return
        self.db.execute('DELETE FROM risk_close_intents WHERE pair=?',(s['pair'],))
        price = money(s['price']) * (1 - money(r.slippage_bps) / 10000)
        quantity = money(p['quantity'])
        gross = price * quantity
        fee = usd(max(money(r.minimum_fee_usd),gross * money(r.fee_bps) / 10000 + money(r.fixed_fee_usd)))
        proceeds = usd(gross) - fee
        cash = money(self.db.execute('SELECT cash FROM account').fetchone()[0])
        self.db.execute("UPDATE positions SET status='closed',closed_at=?,proceeds=?,pnl=? WHERE id=?",
                        (now, str(proceeds), str(proceeds - money(p['cost'])), p['id']))
        self._fill(p['id'], sid, 'sell', now, price, quantity, fee, cash + proceeds)
        self.log(run_id, sid, now, 'RISK', 'sell', [reason])

    def report(self, now=None):
        now = time.time() if now is None else now
        cash = money(self.db.execute('SELECT cash FROM account').fetchone()[0])
        marked = Decimal(0)
        positions = []
        for p in self.open_positions():
            row = self.db.execute('SELECT data FROM snapshots WHERE pair=? ORDER BY observed_at DESC,id DESC LIMIT 1', (p['pair'],)).fetchone()
            s = json.loads(row[0]) if row else {}
            price = number(s.get('price'))
            value = money(p['quantity']) * money(price) if price else None
            if value is not None:
                marked += value
            positions.append(dict(id=p['id'], token=p['token'], pair=p['pair'], symbol=p['symbol'],
                                  cost=p['cost'], mark_usd=str(value) if value is not None else None,
                                  stale=not 0 <= now - s.get('observed_at', 0) <= self.rules.max_snapshot_age))
        pnl = sum((money(row[0]) for row in self.db.execute("SELECT pnl FROM positions WHERE status='closed'")), Decimal(0))
        return dict(mode='paper', cash_usd=str(cash), marked_equity_usd=str(cash + marked),
                    entry_screen='onchain_and_quotes' if self.rules.require_preflight else 'legacy_paper',
                    equity_marks_complete=all(p['mark_usd'] is not None and not p['stale'] for p in positions),
                    realized_pnl_usd=str(pnl), positions=positions,
                    snapshots=self.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0],
                    fills=self.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0], rules_version=self.rules.version)
