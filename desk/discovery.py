"""GeckoTerminal Solana new pools and a durable, bounded candidate queue."""
import time
from datetime import datetime

from .core import number
from .http import JsonHTTP
from .onchain import address


SCHEMA = '''CREATE TABLE IF NOT EXISTS discovery_candidates(
 token TEXT PRIMARY KEY, pool TEXT NOT NULL, created_at REAL NOT NULL,
 first_seen REAL NOT NULL, last_seen REAL NOT NULL, last_checked REAL,
 liquidity REAL, volume_24h REAL, market_cap REAL);
CREATE INDEX IF NOT EXISTS discovery_last_checked ON discovery_candidates(last_checked);'''


class GeckoTerminal:
    base = 'https://api.geckoterminal.com/api/v2'

    def __init__(self, recorder, http=None):
        self.recorder = recorder
        self.http = http or JsonHTTP(spacing=6.1,attempts=2,max_bytes=2*1024*1024)

    def new_pools(self, pages=2):
        if type(pages) is not int or not 1 <= pages <= 2:
            raise ValueError('new-pool pages must be 1 or 2')
        pools = []
        seen = set()
        for page in range(1,pages+1):
            payload,stamp = self.http.request(self.base+'/networks/solana/new_pools?page='+str(page),
                headers={'Accept':'application/json;version=20230203'})
            self.recorder('geckoterminal:new_pools:page:'+str(page),payload,stamp)
            if not isinstance(payload,dict) or not isinstance(payload.get('data'),list) or payload.get('errors'):
                raise ValueError('invalid GeckoTerminal new-pool response')
            if len(payload['data'])>20:
                raise ValueError('unexpected GeckoTerminal page size')
            for row in payload['data']:
                try:
                    item = parse_pool(row,stamp)
                except (ValueError,TypeError,KeyError,AttributeError):
                    self.recorder('geckoterminal:invalid_pool',dict(page=page,reason='invalid_identity_or_creation_time'),stamp)
                    continue
                if item['pool'] not in seen:
                    pools.append(item)
                    seen.add(item['pool'])
            if not payload['data']:
                break
        return pools


    def token_info(self, token):
        token=address(token)
        payload,stamp=self.http.request(self.base+'/networks/solana/tokens/'+token+'/info',
            headers={'Accept':'application/json;version=20230203'})
        self.recorder('geckoterminal:token_info',payload,stamp)
        row=payload.get('data') if isinstance(payload,dict) else None
        if not isinstance(row,dict) or row.get('id')!='solana_'+token or not isinstance(row.get('attributes'),dict):
            raise ValueError('invalid GeckoTerminal token info identity')
        attributes=row['attributes'];count=(attributes.get('holders') or {}).get('count')
        if count is not None and (isinstance(count,bool) or number(count) is None or float(count)!=int(float(count))):
            raise ValueError('invalid indexed holder count')
        return dict(token=token,observed_at=stamp,source='geckoterminal:token_info',
                    holder_count=int(count) if count is not None else None,
                    developer_holding_percentage=attributes.get('developer_holding_percentage'),
                    description=attributes.get('description'),x_handle=clean_handle(attributes.get('twitter_handle')))


def parse_pool(row, now):
    if row.get('type')!='pool':
        raise ValueError('unexpected resource type')
    attributes = row['attributes']
    pool = address(attributes['address'])
    if row.get('id')!='solana_'+pool:
        raise ValueError('pool network mismatch')
    base = row['relationships']['base_token']['data']
    token_id = base['id']
    if base.get('type')!='token' or not token_id.startswith('solana_'):
        raise ValueError('base token network mismatch')
    token = address(token_id[len('solana_'):])
    created = datetime.fromisoformat(attributes['pool_created_at'].replace('Z','+00:00'))
    if created.tzinfo is None:
        raise ValueError('missing creation timezone')
    created_at = created.timestamp()
    if not 0 < created_at <= now:
        raise ValueError('invalid pool creation time')
    def metric(value):
        result = number(value)
        return result if result is not None and result>=0 else None
    return dict(token=token,pool=pool,created_at=created_at,
                liquidity=metric(attributes.get('reserve_in_usd')),
                volume_24h=metric((attributes.get('volume_usd') or {}).get('h24')),
                market_cap=metric(attributes.get('market_cap_usd')))


def queue(desk, pools, limit, now=None, excluded=(), prioritize_market=False):
    """Age before network work; rotate least recently checked, turnover breaks ties."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('candidate limit must be 1..100')
    now = time.time() if now is None else now
    desk.db.executescript(SCHEMA)
    with desk.db:
        desk.db.execute('DELETE FROM discovery_candidates WHERE created_at<?',(now-desk.rules.max_age_hours*3600,))
        # Keep each mint's first observed pool, rather than restarting its clock
        # whenever someone creates another pool for the same existing token.
        for item in sorted(pools,key=lambda p:(p['created_at'],p['pool'])):
            if item['created_at'] < now-desk.rules.max_age_hours*3600:
                continue
            desk.db.execute('''INSERT INTO discovery_candidates
                (token,pool,created_at,first_seen,last_seen,liquidity,volume_24h,market_cap)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET last_seen=excluded.last_seen,
                liquidity=CASE WHEN pool=excluded.pool THEN excluded.liquidity ELSE liquidity END,
                volume_24h=CASE WHEN pool=excluded.pool THEN excluded.volume_24h ELSE volume_24h END,
                market_cap=CASE WHEN pool=excluded.pool THEN excluded.market_cap ELSE market_cap END''',
                (item['token'],item['pool'],item['created_at'],now,now,item['liquidity'],item['volume_24h'],item['market_cap']))
        # An indexed launch window is not an unlimited list of all Solana mints.
        desk.db.execute('''DELETE FROM discovery_candidates WHERE token IN
            (SELECT token FROM discovery_candidates ORDER BY last_seen DESC,first_seen DESC LIMIT -1 OFFSET 500)''')
        candidates = desk.db.execute('''SELECT * FROM discovery_candidates
            WHERE created_at<=? AND created_at>=?
            ORDER BY COALESCE(last_checked,0),
                CASE WHEN market_cap>0 AND volume_24h IS NOT NULL THEN volume_24h/market_cap ELSE -1 END DESC,
                first_seen,token''',(now-desk.rules.min_age_hours*3600,now-desk.rules.max_age_hours*3600)).fetchall()
        candidates=[dict(row) for row in candidates]
        if prioritize_market:
            def priority(c):
                likely=(c['liquidity'] is not None and c['liquidity']>=desk.rules.min_liquidity and
                    c['volume_24h'] is not None and c['volume_24h']>=desk.rules.min_volume_24h and
                    c['market_cap'] is not None and c['market_cap']>=desk.rules.min_market_cap and
                    (not desk.rules.max_market_cap or c['market_cap']<=desk.rules.max_market_cap))
                return (not likely,c['last_checked'] or 0,-((c['volume_24h'] or 0)/max(c['market_cap'] or 0,1)),c['token'])
            candidates.sort(key=priority)
        selected = [row for row in candidates if row['token'] not in excluded][:limit]
        for row in selected:
            desk.db.execute('UPDATE discovery_candidates SET last_checked=? WHERE token=?',(now,row['token']))
        retained = desk.db.execute('SELECT COUNT(*) FROM discovery_candidates').fetchone()[0]
    return selected,dict(source='geckoterminal_solana_new_pools',fetched_pools=len(pools),
                         retained_tokens=retained,age_eligible_tokens=len(candidates),scheduled_tokens=len(selected))


def clean_handle(value):
    import re
    if not isinstance(value,str):return None
    value=value.strip()
    for prefix in ('https://x.com/','https://twitter.com/'):
        if value.startswith(prefix):value=value[len(prefix):]
    value=value.lstrip('@').split('?')[0].split('/')[0]
    return value if re.fullmatch(r'[A-Za-z0-9_]{1,15}',value) else None
