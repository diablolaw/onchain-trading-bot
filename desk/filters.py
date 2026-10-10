"""Measured filter results, separated from unavailable or skipped evidence."""
import json
from collections import Counter

from .core import encode, number, concentration, screening_market


def check(name, group, value, requirement, passed, reason, source, skipped=False, detail=None):
    status = 'skipped' if skipped else 'missing' if value is None else 'pass' if passed else 'fail'
    return dict(check=name,group=group,status=status,value=value,requirement=requirement,
                reason=reason,source=source,detail=detail)


def count(value):
    result = number(value)
    return result if result is not None and result == int(result) else None


def additional_checks(s, r):
    """The same extra checks used by vet and the diagnostic report."""
    rows = []
    if r.min_market_cap or r.max_market_cap:
        cap = number(s.get('market_cap'))
        rows.append(check('market_cap','strategy',cap,dict(min=r.min_market_cap,max=r.max_market_cap or None),
            cap is not None and cap>=r.min_market_cap and (not r.max_market_cap or cap<=r.max_market_cap),
            'market_cap_missing' if cap is None else 'market_cap_out_of_range','dexscreener:marketCap'))
    if r.min_trades_24h:
        buys,sells = count(s.get('buys_24h')),count(s.get('sells_24h'))
        total = buys+sells if buys is not None and sells is not None else None
        rows.append(check('trades_24h','strategy',total,dict(min=r.min_trades_24h),
            total is not None and total>=r.min_trades_24h,
            'trade_count_missing' if total is None else 'too_few_trades','dexscreener:txns.h24'))
    if r.reject_no_sells:
        buys,sells = count(s.get('buys_1h')),count(s.get('sells_1h'))
        value = dict(buys=buys,sells=sells) if buys is not None and sells is not None else None
        rows.append(check('sell_activity','strategy',value,'reject when sells_1h == 0 and buys_1h > 20',
            value is not None and not (sells==0 and buys>20),
            'sell_activity_missing' if value is None else 'buys_without_sells','dexscreener:txns.h1'))
    if r.min_holders:
        holders = count((s.get('holders') or {}).get('owner_count'))
        rows.append(check('holders','strategy',holders,dict(min=r.min_holders),
            holders is not None and holders>=r.min_holders,
            'holder_count_missing' if holders is None else 'too_few_holders','indexer:holder_count' if r.concentration_measure=='token_accounts' else 'solana_rpc:funded_owner_count',
            skipped=holders is None and r.concentration_measure=='token_accounts',detail='Guide skips absent indexed holder count; it is not inferred from top accounts.'))
    return rows


def report(s, r, now, *, check_quotes=True):
    original=s
    s=screening_market(s)
    early = bool(s.get('early_rejection_reasons'))
    created = number(s.get('created_at'))
    age = (now-created)/60 if created is not None else None
    liquidity,volume = number(s.get('liquidity')),number(s.get('volume_24h'))
    rows = [check('pool_age_minutes','strategy',age,dict(min=r.min_age_hours*60,max=r.max_age_hours*60),
                  age is not None and r.min_age_hours*60<=age<=r.max_age_hours*60,'pair_age_out_of_range','dexscreener:pairCreatedAt'),
            check('liquidity_usd','strategy',liquidity,dict(min=r.min_liquidity),liquidity is not None and liquidity>=r.min_liquidity,
                  'low_or_missing_liquidity','dexscreener:liquidity.usd'),
            check('volume_24h_usd','strategy',volume,dict(min=r.min_volume_24h),volume is not None and volume>=r.min_volume_24h,
                  'low_or_missing_volume','dexscreener:volume.h24')]
    extras = additional_checks(s,r)
    for item in extras:
        if early and item['check']=='holders':
            item['status'],item['detail'] = 'skipped','preflight not requested after market rejection'
    rows.extend(extras)
    holders = s.get('holders') or {}
    top,top10 = concentration(holders,r)
    rows.append(check('owner_concentration_structure','execution_safety',
        dict(measurement=r.concentration_measure,largest_pct=top,top10_pct=top10) if top is not None and top10 is not None else None,
        '0 <= largest <= top ten <= 100',top is not None and top10 is not None and 0<=top<=top10<=100,
        'invalid_owner_concentration','solana_rpc:owner_aggregated',skipped=early))
    owner_stamp = number(holders.get('observed_at'))
    owner_age = now-owner_stamp if owner_stamp is not None else None
    rows.append(check('owner_evidence_age_seconds','execution_safety',owner_age,dict(min=0,max=r.max_holder_age),
        owner_age is not None and 0<=owner_age<=r.max_holder_age,'missing_or_stale_owner_evidence','solana_rpc:fetch_time',skipped=early))
    binding = dict(token=holders.get('token'),method=holders.get('method'),source=holders.get('source')) if holders else None
    rows.append(check('owner_evidence_binding','execution_safety',binding,dict(token=s['token'],method=r.concentration_measure,source='required'),
        bool(holders) and holders.get('token')==s['token'] and holders.get('method')==r.concentration_measure and bool(holders.get('source')),
        'missing_or_stale_owner_evidence','solana_rpc:owner_evidence',skipped=early))
    fields=('top_account_pct','top10_account_pct') if r.concentration_measure=='token_accounts' else ('top_owner_pct','top10_owner_pct')
    for field,limit in zip(fields,(r.max_top_owner_pct,r.max_top10_owner_pct)):
        value = number(holders.get(field))
        rows.append(check(field,'strategy',value,dict(max=limit),value is not None and value<=limit,
            'owner_concentration','solana_rpc:owner_aggregated',skipped=early,
            detail='Includes vaults/program owners. Measurement: '+r.concentration_measure+'.'))
    evidence = s.get('preflight') or {}
    if holders.get('top_accounts'):
        rows.append(check('largest_token_account_identity','diagnostic',holders['top_accounts'][0],
            'account identity only; vault classification is not inferred',True,None,'solana_rpc',skipped=early))
    mint = evidence.get('mint') or {}
    for field in ('mint_authority','freeze_authority'):
        known = field in mint
        value = ('revoked' if mint[field] is None else 'active') if known else None
        rows.append(check(field,'strategy',value,'revoked',value=='revoked',field+('_not_revoked' if known else '_evidence_missing'),'solana_rpc:mint',skipped=early))
    if evidence.get('onchain_error_kind')=='metadata_identity_mismatch':
        rows.append(check('metadata_identity','execution_safety',evidence.get('onchain_error'),
            'metadata bound to current mint',False,'metadata_identity_mismatch','solana_rpc:mint',skipped=early))
    if evidence.get('onchain_error_kind')=='unsupported_token_feature':
        rows.append(check('supported_token_feature_validation','execution_safety',evidence.get('onchain_error'),
            'supported token extensions',False,'unsupported_token_features','solana_rpc:mint',skipped=early))
    stamp = number(s.get('observed_at'))
    freshness = now-stamp if stamp is not None else None
    identity = dict(chain=s.get('chain'),token=s.get('token'),pair=s.get('pair')) if s.get('pair') else None
    rows.append(check('market_identity','execution_safety',identity,'Solana mint and pool required',
        s.get('chain')=='solana' and bool(s.get('token')) and bool(s.get('pair')),'invalid_identity','dexscreener:pair'))
    price = number(s.get('price'))
    rows.append(check('price_usd','execution_safety',price,'positive',price is not None and price>0,'missing_price','dexscreener:priceUsd'))
    rows.append(check('market_freshness_seconds','execution_safety',freshness,dict(min=0,max=r.max_snapshot_age),
        freshness is not None and 0<=freshness<=r.max_snapshot_age,'stale_market_data','local_fetch_time'))
    if r.require_preflight:
        mint_stamp = number(mint.get('observed_at'))
        mint_age = now-mint_stamp if mint_stamp is not None else None
        rows.append(check('mint_evidence_age_seconds','execution_safety',mint_age,dict(min=0,max=r.max_holder_age),
            mint_age is not None and 0<=mint_age<=r.max_holder_age,'missing_or_stale_mint_evidence','solana_rpc:fetch_time',skipped=early))
        rows.append(check('mint_evidence_binding','execution_safety',mint.get('token'),s['token'],
            mint.get('token')==s['token'],'missing_or_stale_mint_evidence','solana_rpc:mint',skipped=early))
        rows.append(check('supported_token_features','execution_safety',mint.get('supported_features'),'true',
            mint.get('supported_features') is True,'unsupported_token_features','solana_rpc:mint',skipped=early))
        accounted, supply = holders.get('accounted_supply'),mint.get('supply')
        if r.concentration_measure=='token_accounts':
            accounted=holders.get('supply') if holders.get('supply_verified') else None
        rows.append(check('owner_supply_reconciliation','execution_safety',
            dict(accounted=accounted,supply=supply) if accounted is not None and supply is not None else None,
            'RPC supply verified (largest accounts are a partial distribution)' if r.concentration_measure=='token_accounts' else 'accounted == supply',accounted is not None and accounted==supply,'missing_or_unreconciled_owner_evidence',
            'solana_rpc:all_token_accounts',skipped=early))
        from .preflight import quote_reasons
        quotes = evidence.get('quotes')
        failures = quote_reasons(s['token'],quotes,r,now) if quotes else None
        for side in ('buy','sell'):
            leg = (quotes or {}).get(side) or {}
            for field,limit in (('price_impact_pct',r.max_price_impact_pct),('slippage_bps',r.max_quote_slippage_bps),('fee_bps',r.max_quote_fee_bps)):
                value = number(leg.get(field))
                rows.append(check(side+'_'+field,'execution_safety',value,dict(max=limit),value is not None and value<=limit,
                    side+'_'+field+'_limit','jupiter:'+side,skipped=early))
            stamp = number(leg.get('observed_at'))
            age = now-stamp if stamp is not None else None
            rows.append(check(side+'_quote_age_seconds','execution_safety',age,dict(min=0,max=r.max_preflight_age),
                age is not None and 0<=age<=r.max_preflight_age,side+'_quote_stale','jupiter:'+side,skipped=early))
        rows.append(check('roundtrip_quotes','execution_safety',dict(reasons=failures) if quotes else None,
            dict(max_impact_pct=r.max_price_impact_pct,max_slippage_bps=r.max_quote_slippage_bps,
                 max_fee_bps=r.max_quote_fee_bps,max_roundtrip_loss_pct=r.max_roundtrip_loss_pct,max_age_seconds=r.max_preflight_age),
            failures==[],'roundtrip_evidence_failed','jupiter:roundtrip',skipped=early))
    if not check_quotes:
        for row in rows:
            if row['source'].startswith('jupiter:'):
                row['status']='skipped';row['detail']='Deferred until an actual position size is selected; mandatory before entry.'
    from .core import vet
    blocking = (s.get('early_rejection_reasons') or s.get('scan_rejection_reasons') or vet(original,r,now,check_quotes=check_quotes)) if s.get('pair') else ['market_data_unavailable']
    return dict(token=s['token'],symbol=s.get('symbol','?'),pair=s.get('pair'),observed_at=s.get('observed_at'),
                rules_version=r.version,checks=rows,evidence_errors=list(evidence.get('errors') or []),
                collection_error=s.get('collection_error'),blocking_reasons=blocking,
                concentration_measure=r.concentration_measure,screening_stage='entry' if check_quotes else 'pre_ai',
                evidence_details={k:evidence[k] for k in ('onchain_error','onchain_error_kind','quote_error') if k in evidence})


SCHEMA = '''CREATE TABLE IF NOT EXISTS filter_reports(
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), token TEXT NOT NULL,
 at REAL NOT NULL, data TEXT NOT NULL);'''


def save(desk, run_id, snapshot, now, *, check_quotes=True):
    result = report(snapshot,desk.rules,now,check_quotes=check_quotes)
    with desk.db:
        desk.db.execute('INSERT INTO filter_reports(run_id,token,at,data) VALUES (?,?,?,?)',
                        (run_id,snapshot['token'],now,encode(result)))
    return result


def rejection_report(desk, run_id=None):
    """Latest cycle by default; historical evidence is never silently re-evaluated."""
    if run_id is None:
        row = desk.db.execute('SELECT MAX(run_id) FROM filter_reports').fetchone()
        run_id = row[0]
    rows = desk.db.execute('SELECT data FROM filter_reports WHERE run_id=? ORDER BY id',(run_id,)).fetchall()
    reports = [json.loads(row[0]) for row in rows]
    totals = Counter()
    lines = ['Filter report — run '+str(run_id) if run_id is not None else 'No filter reports recorded yet. Check discovery and the latest AI cycle; a cycle with no candidates produces no filter reports.']
    for item in reports:
        lines.append('\n'+item['symbol']+' | '+item['token'])
        lines.append('Rules: '+item['rules_version'])
        label='Pre-AI screening' if item.get('screening_stage')=='pre_ai' else 'Entry screening'
        lines.append(label+': '+('BLOCKED — '+', '.join(item['blocking_reasons']) if item['blocking_reasons'] else 'PASS (AI/sizing may still reject)'))
        if item.get('screening_stage')=='pre_ai':lines.append('Execution quotes: deferred until actual sizing; mandatory before any entry.')
        if item.get('collection_error'):
            lines.append('  EVIDENCE ERROR: '+item['collection_error'])
        for c in item['checks']:
            totals[c['status']] += 1
            lines.append('  '+c['status'].upper()+' ['+c['group']+'] '+c['check']+
                ': '+json.dumps(c['value'])+'; requires '+json.dumps(c['requirement']))
        for key,value in item.get('evidence_details',{}).items():
            lines.append('  EVIDENCE DETAIL: '+key+' = '+str(value))
        for error in item['evidence_errors']:
            lines.append('  EVIDENCE ERROR: '+error)
    lines.append('\nTotals (checks): '+', '.join(k+'='+str(totals[k]) for k in ('pass','fail','missing','skipped')))
    lines.append('FAIL = measured threshold failure; MISSING = unavailable evidence; SKIPPED = not requested.')
    return '\n'.join(lines)
