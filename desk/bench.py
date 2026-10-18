"""Reject before allocating another dossier; preserve explicit expiry reasons."""
import time
SCHEMA='CREATE TABLE IF NOT EXISTS post_bench(token TEXT PRIMARY KEY,until REAL NOT NULL,reason TEXT NOT NULL)'
# Minutes per rejection reason, from the follow-up post's bench table.
PERMANENT=('honeypot','authority_not_revoked','authority_open','metadata_identity_mismatch','unsupported_token_features','top_wallet','sell_side')
# Evidence that failed to arrive is a dossier failure, not a measured rejection.
TRANSIENT=('invalid_owner_concentration','onchain_evidence_unavailable','missing_or_','stale','unbound','quote_evidence_unavailable','collection')

def reason_minutes(reason):
    if any(s in reason for s in PERMANENT):return 100000
    if any(s in reason for s in ('recycled','account_is_the_project','not_locked')):return 360
    if any(s in reason for s in TRANSIENT):return 45
    if any(s in reason for s in ('top10','holder','dev_still_loaded','concentration')):return 90
    # Judged shape and momentum barely move between cycles: at 25 minutes three tokens took 22 of 35 judge calls.
    if reason in ('shape','shape_weak','momentum_already_spent'):return 120
    # A pullback can arrive at any moment; look again next cycle.
    if reason=='no_recent_pullback':return 9
    if 'age' in reason:return 20
    if any(s in reason for s in ('shape','momentum','liquidity','volume','trades','market_cap')):return 25
    return 45

def minutes(reasons):
    return max(reason_minutes(r) for r in reasons)

def reject(desk,token,reasons,now=None):
    if not reasons or reasons in (['dossier_budget_deferred'],['fomo_evidence_unavailable']):return
    desk.db.execute(SCHEMA);now=time.time() if now is None else now
    with desk.db:desk.db.execute('INSERT OR REPLACE INTO post_bench VALUES (?,?,?)',(token,now+minutes(reasons)*60,','.join(reasons)))

def exclude(desk,candidates,now=None):
    desk.db.execute(SCHEMA);now=time.time() if now is None else now
    blocked={r[0] for r in desk.db.execute('SELECT token FROM post_bench WHERE until>?',(now,))}
    return [c for c in candidates if c['token'] not in blocked]
