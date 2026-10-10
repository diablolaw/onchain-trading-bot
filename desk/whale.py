"""RISK's distributing-whale check, paper only. Reads chain balances; never signs or sends."""
import time
from decimal import Decimal

from .onchain import uint, POOL_AUTHORITIES

SCHEMA='CREATE TABLE IF NOT EXISTS whale_balances(pair TEXT NOT NULL,account TEXT NOT NULL,amount TEXT NOT NULL,at REAL NOT NULL,PRIMARY KEY(pair,account))'


def check(desk,rpc,token,pair,now=None):
    """A non-vault account above the concentration limit whose balance fell since the last check.

    The post does not define distribution; this is the narrowest observable reading of it."""
    desk.db.execute(SCHEMA)
    now=time.time() if now is None else now
    supply,_=rpc.call('getTokenSupply',[token,{'commitment':'confirmed'}])
    supply=uint(supply['value']['amount'])
    largest,_=rpc.call('getTokenLargestAccounts',[token,{'commitment':'confirmed'}])
    rows=[(r['address'],uint(r['amount'])) for r in largest['value'][:20]]
    owners=rpc.owners(token,[a for a,_ in rows])
    limit=Decimal(desk.rules.max_top_owner_pct)/100*supply
    distributing=None
    with desk.db:
        for account,amount in rows:
            owner=owners.get(account)
            if owner==pair or owner in POOL_AUTHORITIES:continue
            previous=desk.db.execute('SELECT amount FROM whale_balances WHERE pair=? AND account=?',(pair,account)).fetchone()
            if previous and int(previous[0])>limit and amount<int(previous[0]) and distributing is None:
                distributing=account
            desk.db.execute('INSERT OR REPLACE INTO whale_balances VALUES (?,?,?,?)',(pair,account,str(amount),now))
    return dict(distributing=distributing is not None,account=distributing,observed_at=now,source='solana_rpc:getTokenLargestAccounts')
