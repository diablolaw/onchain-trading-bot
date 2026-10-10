"""Auditable search approximation and longitudinal comparison, paper only."""
import json
import time
from urllib.parse import quote

from .core import encode, number, normalize, vet
from .onchain import address

SCHEMA = '''CREATE TABLE IF NOT EXISTS discovery_comparison(
 run_id INTEGER NOT NULL, token TEXT NOT NULL, pair TEXT NOT NULL,
 observed_at REAL NOT NULL, data TEXT NOT NULL, PRIMARY KEY(run_id,token));'''


def search(scanner, queries):
    if not 1 <= len(queries) <= 5 or any(not q.strip() or len(q)>80 for q in queries):
        raise ValueError('provide 1..5 search queries, each 1..80 characters')
    found = {}
    for query in queries:
        payload, stamp = scanner.get('/latest/dex/search?q='+quote(query.strip(),safe=''))
        if not isinstance(payload,dict) or not isinstance(payload.get('pairs'),list):
            raise ValueError('invalid DexScreener search response')
        for pair in payload['pairs'][:100]:
            if not isinstance(pair,dict) or pair.get('chainId')!='solana':
                continue
            try:
                token=address((pair.get('baseToken') or {}).get('address'))
                pool=address(pair.get('pairAddress'))
            except ValueError:
                continue
            s=normalize(pair,stamp)
            if s['created_at'] is None or not 0 < s['created_at'] <= stamp:
                continue
            item=dict(token=token,pool=pool,created_at=s['created_at'],liquidity=s['liquidity'],
                      volume_24h=s['volume_24h'],market_cap=s['market_cap'])
            if token not in found or (item['liquidity'] or 0) > (found[token]['liquidity'] or 0):
                found[token]=item
    return list(found.values())


def rank(desk, run_id, snapshots, *, check_quotes=True):
    desk.db.executescript(SCHEMA)
    ranked=[]
    for s in snapshots:
        row=desk.db.execute('''SELECT data FROM discovery_comparison WHERE token=? AND pair=?
            AND observed_at<? AND observed_at>=? ORDER BY observed_at DESC LIMIT 1''',
            (s['token'],s['pair'],s['observed_at'],s['observed_at']-3600)).fetchone()
        previous=json.loads(row[0])['snapshot'] if row else None
        buys,sells=number(s.get('buys_1h')),number(s.get('sells_1h'))
        ratio=buys/max(sells,1) if buys is not None and sells is not None else None
        growth=price_growth=None
        holders=s.get('holders') or {}
        old=(previous or {}).get('holders') or {}
        # Same owner-aggregation definition, mint and source; never substitute buys.
        if (holders.get('method')==old.get('method') and holders.get('method') in ('owner_aggregated','token_accounts')
                and holders.get('source')==old.get('source') and holders.get('owner_count_source')==old.get('owner_count_source') and holders.get('token')==old.get('token')==s['token']):
            a,b=number(old.get('owner_count')),number(holders.get('owner_count'))
            if a and b is not None: growth=(b/a-1)*100
        a,b=number((previous or {}).get('price')),number(s.get('price'))
        if a and b is not None: price_growth=(b/a-1)*100
        if s.get('pre_dossier_ranking'):
            growth=s['pre_dossier_ranking']['holder_growth_pct']
            price_growth=s['pre_dossier_ranking']['price_growth_pct']
        reasons=list(s.get('early_rejection_reasons') or vet(s,desk.rules,time.time(),check_quotes=check_quotes))
        scan_reasons=[]
        windows=s.get('trade_windows') or {}
        if windows:
            recent=[windows.get(k) or {} for k in ('h1','h6')]
            if all(number(w.get('buys')) is not None and number(w.get('sells')) is not None and w['sells']>w['buys'] for w in recent):
                scan_reasons.append('scan_sells_dominate_two_windows')
        elif previous and buys is not None and sells is not None:
            pb,ps=number(previous.get('buys_1h')),number(previous.get('sells_1h'))
            if sells>buys and pb is not None and ps is not None and ps>pb:
                scan_reasons.append('scan_sells_dominate_two_observations')
        if growth is not None and price_growth is not None and price_growth>0 and growth<=0:
            scan_reasons.append('scan_holders_flat_while_price_rises')
        reasons.extend(scan_reasons)
        s['scan_rejection_reasons']=scan_reasons
        warning=growth is not None and price_growth is not None and price_growth>growth
        evidence=dict(holder_growth_pct=growth,price_growth_pct=price_growth,buy_sell_ratio=ratio,
            fallback=growth is None,price_outpaces_holders=warning,blocking_reasons=reasons,
            observation_note='Rolling h1 windows at successive polls overlap; not independent intervals.',
            sources=s.get('discovery',{}).get('streams',[]),
            evidence_details={k:s.get('preflight',{})[k] for k in ('onchain_error','onchain_error_kind','quote_error') if k in s.get('preflight',{})})
        s['scan_ranking']=evidence
        # No boost from price alone. Comparable holder growth leads; ratio is fallback.
        score=(not reasons,growth is not None and not warning,growth if growth is not None and not warning else 0,ratio if ratio is not None else -1)
        ranked.append((score,s))
    ranked.sort(key=lambda item:(*[-float(v) for v in item[0]],item[1]['token']))
    with desk.db:
        for index,(_,s) in enumerate(ranked,1):
            s['scan_ranking']['rank']=index
            desk.db.execute('INSERT OR REPLACE INTO discovery_comparison VALUES (?,?,?,?,?)',
                (run_id,s['token'],s['pair'],s['observed_at'],encode(dict(snapshot=s,ranking=s['scan_ranking'],rules_version=desk.rules.version))))
    return [s for _,s in ranked]


def report(desk):
    desk.db.executescript(SCHEMA)
    latest=desk.db.execute('SELECT MAX(run_id) FROM discovery_comparison').fetchone()[0]
    rows=[json.loads(r[0]) for r in desk.db.execute('SELECT data FROM discovery_comparison WHERE run_id=?',(latest,))]
    rows.sort(key=lambda r:r['ranking']['rank'])
    summary={}
    for source in ('geckoterminal','dexscreener_search'):
        selected=[r for r in rows if source in r['ranking']['sources']]
        summary[source]=dict(observed=len(selected),passed=sum(not r['ranking']['blocking_reasons'] for r in selected))
    history=[json.loads(r[0]) for r in desk.db.execute('SELECT data FROM discovery_comparison ORDER BY observed_at')]
    totals={}
    for source in ('geckoterminal','dexscreener_search'):
        cohort=[r for r in history if source in r['ranking']['sources']]
        moves=[]
        for r in cohort:
            s=r['snapshot']; start=number(s.get('price'))
            if r['ranking']['blocking_reasons'] or not start: continue
            later=desk.db.execute('SELECT data FROM discovery_comparison WHERE token=? AND pair=? AND observed_at BETWEEN ? AND ? ORDER BY observed_at LIMIT 1',
                (s['token'],s['pair'],s['observed_at']+3600,s['observed_at']+5400)).fetchone()
            if later:
                end=number(json.loads(later[0])['snapshot'].get('price'))
                if end is not None: moves.append((end/start-1)*100)
        totals[source]=dict(observations=len(cohort),passed=sum(not r['ranking']['blocking_reasons'] for r in cohort),
            measured_1h_price_changes=len(moves),mean_1h_price_change_pct=sum(moves)/len(moves) if moves else None)
    return dict(run_id=latest,streams=summary,history=totals,candidates=[dict(token=r['snapshot']['token'],symbol=r['snapshot'].get('symbol'),**r['ranking']) for r in rows],
        limitation='Sampled candidates, not all search results. Shared paper account; this is a selection comparison, not independent portfolio P&L. Forward price changes exclude fees, can have repeated tokens, and omit missing follow-ups (survivorship bias).')


def schedule(desk, found, limit, now, excluded=()):
    desk.db.execute("CREATE TABLE IF NOT EXISTS search_candidates(token TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL, last_seen REAL NOT NULL, last_checked REAL)")
    with desk.db:
        desk.db.execute('DELETE FROM search_candidates WHERE created_at<?',(now-desk.rules.max_age_hours*3600,))
        for c in found:
            desk.db.execute("INSERT INTO search_candidates VALUES (?,?,?,?,NULL) ON CONFLICT(token) DO UPDATE SET data=excluded.data,last_seen=excluded.last_seen",
                (c['token'],encode(c),c['created_at'],now))
        desk.db.execute('DELETE FROM search_candidates WHERE token IN (SELECT token FROM search_candidates ORDER BY last_seen DESC,token LIMIT -1 OFFSET 500)')
        rows=desk.db.execute('SELECT token,data FROM search_candidates WHERE created_at BETWEEN ? AND ? ORDER BY COALESCE(last_checked,0),last_seen DESC,token',
            (now-desk.rules.max_age_hours*3600,now-desk.rules.min_age_hours*3600)).fetchall()
        chosen=[json.loads(r['data']) for r in rows if r['token'] not in excluded][:limit]
        for c in chosen:
            desk.db.execute('UPDATE search_candidates SET last_checked=? WHERE token=?',(now,c['token']))
    return chosen,len(rows)


def rank_before_dossiers(desk,candidates,metrics,markets):
    """Comparable FOMO holder/price changes order scarce dossier slots."""
    desk.db.execute('CREATE TABLE IF NOT EXISTS venue_rank_history(token TEXT PRIMARY KEY,observed_at REAL NOT NULL,data TEXT NOT NULL)')
    ranked=[]
    for candidate in candidates:
        token=candidate['token'];current=metrics.get(token) or {};market=markets.get(token) or {}
        row=desk.db.execute('SELECT observed_at,data FROM venue_rank_history WHERE token=?',(token,)).fetchone()
        growth=price_growth=None
        stamp=current.get('observed_at')
        if row and stamp is not None and 0<stamp-row[0]<=3600:
            old=json.loads(row[1])
            if old.get('source')==current.get('source'):
                for field in ('holder_count','price'):
                    first,last=number(old.get(field)),number(current.get(field))
                    change=(last/first-1)*100 if first and last is not None else None
                    if field=='holder_count':growth=change
                    else:price_growth=change
        buys,sells=number(market.get('buys_1h')),number(market.get('sells_1h'))
        ratio=buys/max(sells,1) if buys is not None and sells is not None else -1
        warning=growth is not None and price_growth is not None and price_growth>growth
        evidence=dict(holder_growth_pct=growth,price_growth_pct=price_growth,buy_sell_ratio=ratio,price_outpaces_holders=warning,fallback=growth is None)
        candidate=dict(candidate,pre_dossier_ranking=evidence)
        ranked.append(((growth is not None and not warning,growth if growth is not None and not warning else 0,ratio),candidate))
        if stamp is not None:
            with desk.db:desk.db.execute('INSERT INTO venue_rank_history VALUES (?,?,?) ON CONFLICT(token) DO UPDATE SET observed_at=excluded.observed_at,data=excluded.data WHERE excluded.observed_at>observed_at',(token,stamp,encode(current)))
    ranked.sort(key=lambda item:(*[-float(v) for v in item[0]],item[1]['token']))
    return [candidate for _,candidate in ranked]
