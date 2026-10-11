"""Durable tasks bound to the project handle published by the token indexer."""
import json
import time
from .core import encode
from .discovery import clean_handle

SCHEMA='''CREATE TABLE IF NOT EXISTS social_jobs(token TEXT PRIMARY KEY,handle TEXT NOT NULL,at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS social_evidence(token TEXT PRIMARY KEY,handle TEXT NOT NULL,observed_at REAL NOT NULL,data TEXT NOT NULL);'''

def request(desk,token,handle):
    desk.db.executescript(SCHEMA)
    handle=clean_handle(handle)
    if not handle:return None
    now=time.time()
    row=desk.db.execute('SELECT data,observed_at FROM social_evidence WHERE token=? AND lower(handle)=lower(?) AND observed_at>=? AND observed_at<=?',(token,handle,now-3600,now)).fetchone()
    # Evidence in the first half of its hour needs no new job; asking again only spent the seat's turns.
    if row and now-row[1]<1800:return json.loads(row[0])
    # Keep the original request time while the job is live, so callers can bound how long they wait.
    with desk.db:desk.db.execute('INSERT INTO social_jobs VALUES (?,?,?) ON CONFLICT(token) DO UPDATE SET at=CASE WHEN lower(handle)=lower(excluded.handle) AND at>=? THEN at ELSE excluded.at END,handle=excluded.handle',(token,handle,now,now-3600))
    return json.loads(row[0]) if row else None


def jobs(db):
    db.executescript(SCHEMA)
    return [dict(token=r[0],handle=r[1]) for r in db.execute('SELECT token,handle FROM social_jobs WHERE at>=? ORDER BY at LIMIT 10',(time.time()-3600,))]


def submit(db,data):
    db.executescript(SCHEMA)
    token=data.get('token');handle=clean_handle(data.get('handle'));stamp=data.get('observed_at')
    row=db.execute('SELECT handle FROM social_jobs WHERE token=?',(token,)).fetchone()
    if not row or not handle or row[0].lower()!=handle.lower() or type(stamp) not in (int,float) or not 0<=time.time()-stamp<=3600:raise ValueError('unbound or stale social evidence')
    if data.get('source') not in ('grok_bot_x_plugin','grok_bot_public_x') or not isinstance(data.get('x_account'),dict):raise ValueError('social evidence requires Grok X source')
    account=data['x_account']
    if clean_handle(account.get('handle'))!=handle:raise ValueError('account handle mismatch')
    # Evidence fields only; never accept arbitrary browser/account credential state.
    allowed={'handle','created_at','follower_count','recent_posts','previous_handles','bio','project_links'}
    if set(account)-allowed or len(encode(account).encode())>12000:raise ValueError('invalid social account fields')
    evidence=dict(token=token,handle=handle,observed_at=stamp,source=data['source'],x_account=account)
    with db:
        db.execute('INSERT OR REPLACE INTO social_evidence VALUES (?,?,?,?)',(token,handle,stamp,encode(evidence)))
        db.execute('DELETE FROM social_jobs WHERE token=?',(token,))
    return dict(accepted=True,token=token)


def requested_at(desk,token):
    desk.db.executescript(SCHEMA)
    row=desk.db.execute('SELECT at FROM social_jobs WHERE token=?',(token,)).fetchone()
    return row[0] if row else None
