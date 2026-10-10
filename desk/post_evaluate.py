"""Jev-only paper admission for the post profile. No execution credentials."""
import json
import time
from dataclasses import asdict,replace
from .ai import init,audit,state_for
from .core import encode,vet
from .post_questions import questions
from .post_policy import soft_reasons,pick_passed,size,fixed_size
from .filters import save
from .bench import reject

# The post reads X inside the dossier. Evidence arrives asynchronously from the Grok SOCIAL seat,
# so a candidate with a published handle waits this long for it before proceeding unvetted.
SOCIAL_WAIT_SECONDS=1800


def evaluate(desk,run_id,snapshots,providers,policy,refresh_selected=None):
    if not desk.rules.require_preflight:raise ValueError('post profile requires preflight')
    init(desk);providers.ready()
    desk.db.execute('CREATE TABLE IF NOT EXISTS post_bench(token TEXT PRIMARY KEY,until REAL NOT NULL,reason TEXT NOT NULL)')
    with desk.db:desk.db.execute('INSERT INTO ai_cycles VALUES (?,?,?,?,NULL,?,NULL,NULL)',(run_id,policy.version,encode(asdict(policy)),time.time(),'running'))
    shortlist=[];selected=None;state='complete';entry_size=None;judge_errors=0
    original_rules=desk.rules
    liquidity_fraction=desk.rules.max_ticket_liquidity_pct/100
    def judged(token,stage,state,name):
        # A failed judge skips this candidate without benching it, as the post specifies.
        nonlocal judge_errors
        try:answer=providers.judge(state,questions(name,state))
        except Exception as error:
            judge_errors+=1;audit(desk,run_id,token,'judge_failed',dict(stage=stage,error_type=type(error).__name__));return None
        audit(desk,run_id,token,stage,answer);return answer
    try:
        for snapshot in snapshots:
            token=snapshot['token'];save(desk,run_id,snapshot,time.time(),check_quotes=False)
            if desk.open_positions():
                audit(desk,run_id,token,'post_skip',dict(reason='one_position_already_open'));continue
            bench=desk.db.execute('SELECT until,reason FROM post_bench WHERE token=?',(token,)).fetchone()
            if bench and bench[0]>time.time():
                audit(desk,run_id,token,'post_bench',dict(until=bench[0],reason=bench[1]));continue
            reasons=snapshot.get('early_rejection_reasons') or snapshot.get('scan_rejection_reasons') or vet(snapshot,desk.rules,time.time(),check_quotes=False)
            if reasons:
                reject(desk,token,reasons)
                audit(desk,run_id,token,'deterministic_reject',dict(reasons=reasons));continue
            if len(shortlist)>=policy.max_candidates:continue
            dossier=state_for(desk,snapshot)
            if len(dossier['observations'])<2:
                audit(desk,run_id,token,'warmup',dict(reason='need_two_observations'));continue
            mint=(snapshot.get('preflight') or {}).get('mint') or {};holders=snapshot.get('holders') or {};indexed=snapshot.get('indexed_dossier') or {}
            dossier.update(liquidity_usd=snapshot['liquidity'],change=snapshot.get('price_change_windows'),
                mint_authority=mint.get('mint_authority'),freeze_authority=mint.get('freeze_authority'),
                top_wallet_percent=holders.get('top_account_pct'),top_10_percent=holders.get('top10_account_pct'),
                holder_count=holders.get('owner_count'),developer_holding_percentage=indexed.get('developer_holding_percentage'),
                intended_ticket_usd=min(desk.rules.ticket,float(desk.db.execute('SELECT cash FROM account WHERE id=1').fetchone()[0])*.06,snapshot['liquidity']*liquidity_fraction),ticket_basis='upper bound before Kelly sizing; execution quotes required after sizing',concentration_measure='largest token accounts including unidentified vaults; not aggregated wallet ownership')
            market_state={k:dossier.get(k) for k in ('observations','changes','holder_growth','volume_ratio_6h','change','liquidity_usd','intended_ticket_usd','ticket_basis')}
            market_state['trade_windows']=snapshot.get('trade_windows')
            job=snapshot.get('social_job') or {}
            if indexed.get('x_handle') and not snapshot.get('social_evidence') and type(job.get('requested_at')) in (int,float) and time.time()-job['requested_at']<SOCIAL_WAIT_SECONDS:
                audit(desk,run_id,token,'post_social_pending',dict(handle=indexed['x_handle'],waited_seconds=int(time.time()-job['requested_at'])));continue
            market=judged(token,'post_market',market_state,'market')
            if market is None:continue
            chain_state={k:dossier.get(k) for k in ('mint_authority','freeze_authority','top_wallet_percent','top_10_percent','holder_count','developer_holding_percentage')}
            chain_state['concentration_unit']='percentage_points_0_to_100'
            chain_state['concentration_measure']=dossier['concentration_measure']
            chain=judged(token,'post_chain_solana',chain_state,'chain_solana')
            if chain is None:continue
            social=None
            if snapshot.get('social_evidence'):
                social_state=dict(snapshot['social_evidence'],token=dict(ticker=snapshot.get('symbol'),narrative=indexed.get('description')),published_handle=indexed.get('x_handle'))
                social=judged(token,'post_social',social_state,'social')
                if social is None:continue
            reasons=soft_reasons(market['answers'],chain['answers'],social['answers'] if social else None)
            if reasons:
                audit(desk,run_id,token,'post_soft_reject',dict(reasons=reasons))
                reject(desk,token,reasons)
                continue
            # The post's 0.6 cut is for "no usable X account". WorldMonitor state is recorded, not multiplied.
            shortlist.append(dict(token=token,summary=encode(dict(market=market['answers'],chain=chain['answers'],social=social['answers'] if social else None)),evidence=dossier,snapshot=snapshot,partial=social is None,world_degraded=bool((snapshot.get('world_context') or {}).get('degraded',True))))
        if len(shortlist)==1:
            # A single survivor skips the pick and its gate, as in the post.
            selected=shortlist[0]['token'];audit(desk,run_id,selected,'post_pick',dict(reason='single_survivor_skips_pick'))
        elif shortlist:
            candidates=[dict(token=c['token'],summary=c['summary'],evidence=c['evidence']) for c in shortlist]
            pick=judged(None,'post_pick',dict(candidates=candidates),'pick')
            if pick and pick_passed(pick['answers']):selected=pick['answers']['best']['choice']
        if selected:
            chosen=next(c for c in shortlist if c['token']==selected);e=chosen['snapshot'].get('sizing_evidence')
            cash=float(desk.db.execute('SELECT cash FROM account WHERE id=1').fetchone()[0])
            explicit=isinstance(e,dict) and isinstance(e.get('source'),str) and e['source'] and type(e.get('observed_at')) in (int,float) and 0<=time.time()-e['observed_at']<=3600
            if chosen['snapshot'].get('observe_only'):
                audit(desk,run_id,selected,'post_size',dict(reason='observe_only'));selected=None
            elif chosen['snapshot'].get('entries_blocked'):
                audit(desk,run_id,selected,'post_size',dict(reason=chosen['snapshot']['entries_blocked']));selected=None
            elif explicit:
                entry_size=size(cash,cash,e['probability'],e['payoff'],chosen['snapshot']['liquidity'],partial=chosen['partial'],locked_cash=e.get('locked_cash',0),operating_reserve=e.get('operating_reserve',0),max_fee_pct=e.get('max_fee_pct',3),liquidity_fraction=liquidity_fraction)
                audit(desk,run_id,selected,'post_size',entry_size)
            elif desk.rules.paper_fixed_fraction:
                entry_size=fixed_size(cash,cash,desk.rules.paper_fixed_fraction,chosen['snapshot']['liquidity'],partial=chosen['partial'],liquidity_fraction=liquidity_fraction)
                audit(desk,run_id,selected,'post_size',entry_size)
            else:
                audit(desk,run_id,selected,'post_size',dict(reason='missing_fresh_explicit_kelly_inputs'));selected=None
            if selected:
                if entry_size['dollars']<=0 or entry_size['dollars']>desk.rules.ticket:
                    audit(desk,run_id,selected,'post_size_block',dict(reason='zero_size_or_exceeds_preflight_ticket'));selected=None
        if selected:
            if not refresh_selected:
                audit(desk,run_id,selected,'post_size_block',dict(reason='sized_order_requires_fresh_quotes'));selected=None
            else:
                desk.rules=replace(original_rules,ticket=entry_size['dollars'])
                try:
                    fresh=refresh_selected(next(s for s in snapshots if s['token']==selected))
                    original=next(s for s in snapshots if s['token']==selected)
                    reasons=vet(fresh,desk.rules,time.time())
                    audit(desk,run_id,selected,'entry_refresh',dict(ticket=desk.rules.ticket,reasons=reasons))
                    if fresh.get('token')!=selected or fresh.get('pair')!=original['pair'] or reasons:selected=None
                    else:snapshots=[fresh if s['token']==selected else s for s in snapshots]
                finally:desk.rules=original_rules
    except Exception as error:
        state='blocked';selected=None;audit(desk,run_id,None,'error',dict(error_type=type(error).__name__))
    if state=='complete' and judge_errors:state='degraded'
    original_rules=desk.rules
    try:
        if selected:desk.rules=replace(desk.rules,ticket=entry_size['dollars'])
        for s in snapshots:
            row=desk.db.execute('SELECT id FROM ai_decisions WHERE run_id=? AND token=? ORDER BY id DESC LIMIT 1',(run_id,s['token'])).fetchone()
            s=dict(s,ai_policy_version=policy.version,ai_decision_id=row[0] if row else None)
            desk.record(run_id,s,time.time(),enter=state!='blocked' and s['token']==selected)
    finally:desk.rules=original_rules
    summary=dict(mode='paper_only',state=state,selected_token=selected,shortlisted=len(shortlist),calls=providers.calls,policy_version=policy.version,rules_version=desk.rules.version,ready_for_live=False,paper_buys=desk.db.execute("SELECT COUNT(*) FROM fills f JOIN snapshots s ON s.id=f.snapshot_id WHERE s.run_id=? AND f.side='buy'",(run_id,)).fetchone()[0])
    with desk.db:desk.db.execute('UPDATE ai_cycles SET finished_at=?,state=?,selected_token=?,summary=? WHERE run_id=?',(time.time(),state,selected,encode(summary),run_id))
    return summary
