"""Reject before allocating another dossier; preserve explicit expiry reasons."""
import time
SCHEMA='CREATE TABLE IF NOT EXISTS post_bench(token TEXT PRIMARY KEY,until REAL NOT NULL,reason TEXT NOT NULL)'
def minutes(reasons):
    text=','.join(reasons)
    if any(s in text for s in ('honeypot','authority_not_revoked','authority_open','metadata_identity_mismatch')):return 100000
    if any(s in text for s in ('recycled','account_is_the_project')):return 360
    if any(s in text for s in ('concentration','holder','dev_still_loaded')):return 90
    if 'age' in text:return 20
    return 25

def reject(desk,token,reasons,now=None):
    if not reasons or reasons in (['dossier_budget_deferred'],['fomo_evidence_unavailable']):return
    desk.db.execute(SCHEMA);now=time.time() if now is None else now
    with desk.db:desk.db.execute('INSERT OR REPLACE INTO post_bench VALUES (?,?,?)',(token,now+minutes(reasons)*60,','.join(reasons)))

def exclude(desk,candidates,now=None):
    desk.db.execute(SCHEMA);now=time.time() if now is None else now
    blocked={r[0] for r in desk.db.execute('SELECT token FROM post_bench WHERE until>?',(now,))}
    return [c for c in candidates if c['token'] not in blocked]
