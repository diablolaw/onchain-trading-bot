"""Independent launch discovery. No Jev, wallet, quote, or trading calls."""
import json
import time
from urllib.parse import quote
from .core import normalize, encode, number
from .onchain import address
from .scanner import DexScreener

SCHEMA='''CREATE TABLE IF NOT EXISTS launch_feed(
 token TEXT PRIMARY KEY, observed_at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS launch_observations(token TEXT NOT NULL,pair TEXT NOT NULL,observed_at REAL NOT NULL,data TEXT NOT NULL,PRIMARY KEY(token,pair,observed_at));
CREATE INDEX IF NOT EXISTS launch_observations_time ON launch_observations(observed_at);
CREATE TABLE IF NOT EXISTS launch_watch(token TEXT PRIMARY KEY,created_at REAL NOT NULL,last_refreshed REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS launch_sources(name TEXT PRIMARY KEY,at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS launch_feed_status(
 id INTEGER PRIMARY KEY CHECK(id=1), observed_at REAL NOT NULL, data TEXT NOT NULL);'''


def init(desk):desk.db.executescript(SCHEMA)


def poll(desk,queries,scanner=None,now=None,gecko=None):
    if not 1<=len(queries)<=5 or any(not q.strip() or len(q)>80 for q in queries):
        raise ValueError('provide 1..5 search queries of 1..80 characters')
    init(desk)
    run_id=desk.run('launch-discovery',time.time())
    scanner=scanner or DexScreener(lambda source,data,stamp:desk.raw(run_id,source,data,stamp),attempts=1,timeout=4)
    found={};failed=[];stamp=time.time() if now is None else now
    launches=[]
    if gecko is not None:
        last=desk.db.execute("SELECT at FROM launch_sources WHERE name='gecko'").fetchone()
        if last is None or stamp-last[0]>=300:
            try:launches=gecko.new_pools()
            except Exception as error:failed.append(dict(query='geckoterminal',error_type=type(error).__name__))
            try:launches=launches+gecko.trending_pools()
            except Exception as error:failed.append(dict(query='geckoterminal_trending',error_type=type(error).__name__))
            with desk.db:desk.db.execute("INSERT OR REPLACE INTO launch_sources VALUES ('gecko',?)",(stamp,))
    for query in queries:
        try:
            payload,stamp=scanner.get('/latest/dex/search?q='+quote(query.strip(),safe=''))
            if not isinstance(payload,dict) or not isinstance(payload.get('pairs'),list):raise ValueError('invalid search response')
            for pair in payload['pairs'][:100]:
                if not isinstance(pair,dict) or pair.get('chainId')!='solana':continue
                try:
                    address((pair.get('baseToken') or {}).get('address'));address(pair.get('pairAddress'))
                except ValueError:continue
                s=normalize(pair,stamp)
                if s['created_at'] is None or not 0<s['created_at']<=stamp:continue
                if s['token'] not in found or (s['liquidity'] or 0)>(found[s['token']]['liquidity'] or 0):found[s['token']]=s
        except Exception as error:failed.append(dict(query=query,error_type=type(error).__name__))
    with desk.db:
        for item in [*launches,*found.values()]:
            desk.db.execute('INSERT INTO launch_watch(token,created_at) VALUES (?,?) ON CONFLICT(token) DO UPDATE SET created_at=min(created_at,excluded.created_at)',(item['token'],item['created_at']))
        desk.db.execute('DELETE FROM launch_watch WHERE created_at<?',(stamp-desk.rules.max_age_hours*3600,))
        desk.db.execute('DELETE FROM launch_watch WHERE token IN (SELECT token FROM launch_watch ORDER BY created_at DESC,token LIMIT -1 OFFSET 500)')
    # Refresh watched mints even when they disappear from search. Rotate work.
    refresh=[r[0] for r in desk.db.execute('SELECT token FROM launch_watch ORDER BY last_refreshed,token LIMIT 60') if r[0] not in found]
    if refresh:
        try:
            for token,snapshot in scanner.batch_pairs(refresh).items():
                if snapshot is not None:found[token]=snapshot
        except Exception as error:failed.append(dict(query='watch_refresh',error_type=type(error).__name__))
        finally:
            with desk.db:desk.db.executemany('UPDATE launch_watch SET last_refreshed=? WHERE token=?',[(stamp,t) for t in refresh])
    stamp=time.time() if now is None else now
    with desk.db:
        for s in found.values():
            desk.db.execute('UPDATE launch_watch SET last_refreshed=? WHERE token=?',(s['observed_at'],s['token']))
            desk.db.execute('INSERT OR REPLACE INTO launch_feed VALUES (?,?,?)',(s['token'],s['observed_at'],encode(s)))
            desk.db.execute('INSERT OR IGNORE INTO launch_observations VALUES (?,?,?,?)',(s['token'],s['pair'],s['observed_at'],encode(s)))
        desk.db.execute('DELETE FROM launch_observations WHERE observed_at<?',(stamp-3600,))
        desk.db.execute('DELETE FROM launch_feed WHERE observed_at<?',(stamp-3600,))
        desk.db.execute('DELETE FROM launch_feed WHERE token IN (SELECT token FROM launch_feed ORDER BY observed_at DESC,token LIMIT -1 OFFSET 500)')
        status=dict(source='geckoterminal_and_dexscreener_watch' if gecko else 'dexscreener_search',observed=len(found),queries=queries,errors=failed,degraded=bool(failed),market_only=True)
        desk.db.execute('INSERT OR REPLACE INTO launch_feed_status VALUES (1,?,?)',(stamp,encode(status)))
    return status


def candidates(desk,limit=100,excluded=(),now=None):
    init(desk);now=time.time() if now is None else now
    rows=desk.db.execute('SELECT data FROM launch_feed WHERE observed_at>=? AND observed_at<=? ORDER BY observed_at DESC,token',(now-120,now)).fetchall()
    eligible=[];invalid_age=0
    for row in rows:
        s=json.loads(row[0]);created=number(s.get('created_at'))
        # Refreshed pairs can omit creation time even when discovery had it.
        # Do not borrow the age of a potentially different pool for this mint.
        if created is None or not 0<created<=now:
            invalid_age+=1
            continue
        age=now-created
        if s['token'] in excluded or not desk.rules.min_age_hours*3600<=age<=desk.rules.max_age_hours*3600:continue
        eligible.append(dict(token=s['token'],pool=s['pair'],created_at=s['created_at'],liquidity=s['liquidity'],volume_24h=s['volume_24h'],market_cap=s['market_cap']))
    eligible.sort(key=lambda c:(-(c['volume_24h'] or 0),c['token']))
    status=desk.db.execute('SELECT observed_at,data FROM launch_feed_status WHERE id=1').fetchone()
    healthy=bool(status and 0<=now-status['observed_at']<=65 and not json.loads(status['data'])['degraded'])
    return eligible[:limit],dict(source='dexscreener_30s_feed',retained_tokens=len(rows),invalid_age_tokens=invalid_age,age_eligible_tokens=len(eligible),scheduled_tokens=min(limit,len(eligible)),fetch_failed=not healthy)
