"""Local paper orchestration; no live engine imports or wallet access."""
import json
import math
import os
import time
from dataclasses import asdict
from pathlib import Path

from .comparison import search as search_candidates, rank as rank_candidates, report as comparison_report, schedule as schedule_search
from .fomo import Bridge as FomoBridge, setup as fomo_setup, status as fomo_status, enrich as fomo_enrich
from .ai import AIPolicy, AIProviders, evaluate, init
from .core import vet, encode
from .discovery import GeckoTerminal, queue
from .filters import rejection_report, save as save_filters
from .http import JsonHTTP
from .onchain import SolanaRPC, PostSolanaRPC
from .preflight import collect, preflight_reasons
from .quotes import Jupiter
from .scanner import DexScreener, best_pair


def market_fields(fresh):
    """A re-fetched pair observation, without the empty holder fields that would erase the dossier's evidence."""
    return {k:v for k,v in fresh.items() if k not in ('holders','concentration_measure')}


def add_commands(parser, sub):
    parser.add_argument('--ai-config',help='JSON AI policy overrides')
    sub.add_parser('fomo-setup',help='Print local extension pairing key; not a FOMO credential')
    sub.add_parser('fomo-bridge',help='Run the market-only local bridge for connection testing')
    probe=sub.add_parser('fomo-check',help='Verify one token market response through Chrome; no AI calls or trades')
    probe.add_argument('token',help='Solana token mint')
    sub.add_parser('fomo-status',help='Read local bridge connection status')
    runner=sub.add_parser('post-run',help='Supervise separate 30s discovery, post Jev paper decisions, 5min risk and judge service')
    runner.add_argument('--post-db',default='data/post-paper.sqlite')
    runner.add_argument('--decision-interval',type=int,default=900)
    runner.add_argument('--worldmonitor-url')
    runner.add_argument('--sizing-file')
    runner.add_argument('--observe-only',action='store_true')
    runner.add_argument('--paper-config',default='post-paper.config.json',help='Paper rules for this account, e.g. post-paper-100.config.json')
    runner.add_argument('--social-port',type=int,help='Also supervise the social-only Grok evidence service on this loopback port (tunnel runs separately)')
    book=sub.add_parser('book-scan',help='Original BOOK public quotes and explicitly supplied forecasts; no orders')
    book.add_argument('--forecasts-file')
    book.add_argument('--max-spread',type=float,default=.05)
    book.add_argument('--max-ticket',type=float,default=60)
    sub.add_parser('judge-setup',help='Print the local desk secret for cloud seat configuration; never the Jev key')
    judge=sub.add_parser('judge-serve',help='Run the authenticated local Jev question service')
    judge.add_argument('--port',type=int,default=8080)
    judge.add_argument('--social-only',action='store_true',help='Dedicated social credential; disable paid judge calls')
    wm=sub.add_parser('worldmonitor-status',help='Verify local WorldMonitor context and prediction availability')
    wm.add_argument('--url',default='http://127.0.0.1:3000')
    feed=sub.add_parser('discovery-watch',help='Independent DexScreener discovery, default every 30 seconds; no AI or trades')
    feed.add_argument('--interval',type=float,default=30)
    feed.add_argument('--search-query',action='append')
    sub.add_parser('ai-status',help='Local configuration and latest paper AI cycles; no API calls')
    sub.add_parser('ai-decisions',help='Display recorded AI judgments and selection evidence')
    report = sub.add_parser('ai-report',help='Readable measured filter report, latest recorded cycle by default')
    report.add_argument('--run-id',type=int,help='Show the stored checks for a specific historical run')
    sub.add_parser('discovery-report',help='Compare the latest recorded discovery streams and ranking')
    scan = sub.add_parser('ai-scan',help='One Jev/Grok paper cycle; never signs or sends transactions')
    scan.add_argument('--limit',type=int,default=5)
    watch = sub.add_parser('ai-watch',help='Continuous Jev/Grok paper cycles; run paper risk separately')
    watch.add_argument('--limit',type=int,default=5)
    watch.add_argument('--interval',type=float,default=300)
    for command in (scan,watch):
        command.add_argument('--compare-discovery',action='store_true',help='Compare GeckoTerminal with configurable DexScreener search; rank their union')
        command.add_argument('--observe-only',action='store_true',help='Record judgments without paper entries')
        command.add_argument('--sizing-file',help='Explicit per-mint probability/payoff evidence for post Kelly sizing')
        command.add_argument('--worldmonitor-url',help='Local WorldMonitor origin; unavailable context is explicit')
        command.add_argument('--discovery-feed',action='store_true',help='Consume the independent 30-second DexScreener feed instead of fetching discovery')
        command.add_argument('--fomo',action='store_true',help='Require Chrome FOMO market enrichment; no silent fallback')
        command.add_argument('--market-limit',type=int,default=100,help='Batch market observations per stream, 1..100; dossier --limit remains separate')
        command.add_argument('--search-query',action='append',help='Repeat up to five times; default: pump, raydium')


COMMANDS = {'ai-status','ai-decisions','ai-report','ai-scan','ai-watch','discovery-report','fomo-setup','fomo-status','fomo-bridge','fomo-check','discovery-watch','worldmonitor-status','judge-setup','judge-serve','book-scan','post-run'}


def run(args,desk):
    from .cli import monitor
    if args.command=='post-run':
        from .supervisor import run as supervise
        return supervise(args.post_db,args.decision_interval,args.worldmonitor_url,args.sizing_file,args.observe_only,args.social_port,args.paper_config)
    if args.command=='book-scan':
        from .books import collect as collect_books,evaluate as evaluate_books
        if not math.isfinite(args.max_spread) or not 0<=args.max_spread<=1 or not math.isfinite(args.max_ticket) or args.max_ticket<=0:raise ValueError('invalid book limits')
        forecasts=json.loads(Path(args.forecasts_file).read_text()) if args.forecasts_file else {}
        if not isinstance(forecasts,dict):raise ValueError('forecasts must map exact venue:market_id to evidence')
        data=collect_books()
        data['judgments']=evaluate_books(data['markets'],forecasts,args.max_spread,args.max_ticket)
        data['forecast_status']='explicit_inputs' if forecasts else 'missing_independent_forecasts'
        data['execution']='not_connected'
        return data
    if args.command=='judge-setup':
        from .judge_service import setup
        return 'Desk secret (configure only in your own bots): '+setup()['secret']
    if args.command=='judge-serve':
        from .judge_service import serve
        return serve(args.port,args.db,args.social_only)
    if args.command=='worldmonitor-status':
        from .worldmonitor import WorldMonitor
        return WorldMonitor(args.url).health()
    if args.command=='discovery-watch':
        from .discovery_feed import poll
        if not math.isfinite(args.interval) or args.interval<30:raise ValueError('discovery interval must be at least 30 seconds')
        gecko=GeckoTerminal(lambda *args:None)
        try:
            while True:
                start=time.monotonic()
                print(json.dumps(poll(desk,args.search_query or ['pump','raydium'],gecko=gecko)),flush=True)
                time.sleep(max(0,args.interval-(time.monotonic()-start)))
        except KeyboardInterrupt:return dict(stopped=True,market_only=True)
    policy = AIPolicy(**json.loads(Path(args.ai_config).read_text())) if args.ai_config else AIPolicy()
    init(desk)
    if args.command=='fomo-setup':
        config=fomo_setup()
        return 'Local pairing key (paste only into the extension options): '+config['key']
    if args.command=='fomo-check':
        from .onchain import address
        token=address(args.token)
        bridge=FomoBridge().start()
        try:
            metrics=bridge.tokens([token],timeout=60)
            return dict(market_only=True,token=token,returned=token in metrics,metrics=metrics.get(token),connection=bridge.status())
        finally:
            bridge.close()
    if args.command=='fomo-bridge':
        bridge=FomoBridge().start()
        print('FOMO market bridge listening on 127.0.0.1:8766; Ctrl+C to stop.',flush=True)
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            return dict(stopped=True,market_only=True)
        finally:
            bridge.close()
    if args.command=='fomo-status':
        return fomo_status()
    if args.command=='ai-status':
        return dict(mode='paper_only',credentials={k:bool(os.environ.get(k)) for k in ('TYPESAFE_API_KEY','XAI_API_KEY')},
                    policy=asdict(policy),policy_version=policy.version,
                    recent_cycles=[dict(row) for row in desk.db.execute('SELECT * FROM ai_cycles ORDER BY run_id DESC LIMIT 10')],
                    integrations=dict(jev='HTTP API',grok='analysis API, not cloud Grok Bot seats',fomo=fomo_status(),
                                      discovery='GeckoTerminal Solana new pools, two pages; persisted queue',
                                      social='not connected',worldmonitor='not connected',prediction_markets='not connected'))
    if args.command=='ai-decisions':
        return [dict(row) for row in desk.db.execute('SELECT * FROM ai_decisions ORDER BY id DESC LIMIT 100')]
    if args.command=='discovery-report':
        return comparison_report(desk)
    if args.command=='ai-report':
        return rejection_report(desk,args.run_id)
    if not desk.rules.require_preflight:
        raise ValueError('AI paper cycles require automatic preflight')
    if args.command=='ai-watch' and (not math.isfinite(args.interval) or args.interval<60):
        raise ValueError('AI watch interval must be at least 60 seconds')
    if not 1<=getattr(args,'market_limit',100)<=100 or not 1<=args.limit<=100:
        raise ValueError('market and dossier limits must be 1..100')
    # Missing credentials stop before provider calls, without exposing values.
    AIProviders(lambda *a:None,policy).ready()
    bridge=FomoBridge().start() if getattr(args,'fomo',False) else None
    try:
        while True:
            start = time.monotonic()
            run_id = desk.run('ai-paper',time.time())
            recorder = lambda source,data,stamp:desk.raw(run_id,source,data,stamp)
            scanner = DexScreener(recorder)
            monitor(desk,scanner,run_id)
            snapshots = []
            open_tokens = {p['token'] for p in desk.open_positions()}
            discovery_error = None
            gecko=GeckoTerminal(recorder)
            if getattr(args,'discovery_feed',False):
                if getattr(args,'compare_discovery',False):raise ValueError('choose --discovery-feed or --compare-discovery')
                from .discovery_feed import candidates as feed_candidates,wait_for_first_poll
                wait_for_first_poll(desk)
                candidates,discovery=feed_candidates(desk,args.market_limit,open_tokens)
                streams={c['token']:{'dexscreener_search'} for c in candidates}
                if discovery['fetch_failed']:discovery_error='discovery_feed_degraded'
            else:
                try:
                    pools = gecko.new_pools()
                except Exception as error:
                    pools = []
                    discovery_error = type(error).__name__
                    recorder('geckoterminal:discovery_error',dict(error_type=discovery_error),time.time())
                market_limit=getattr(args,'market_limit',args.limit)
                candidates,discovery = queue(desk,pools,market_limit,excluded=open_tokens,prioritize_market=True)
                discovery['fetch_failed'] = discovery_error is not None
                recorder('geckoterminal:queue',discovery,time.time())
                streams={c['token']:{'geckoterminal'} for c in candidates}
                if getattr(args,'compare_discovery',False):
                    try:
                        found=search_candidates(scanner,getattr(args,'search_query',None) or ['pump','raydium'])
                        baseline_tokens={c['token'] for c in candidates}
                        eligible,eligible_count=schedule_search(desk,found,market_limit,time.time(),open_tokens)
                        for c in eligible:
                            streams.setdefault(c['token'],set()).add('dexscreener_search')
                            if c['token'] not in baseline_tokens:
                                candidates.append(c)
                        discovery['search']=dict(queries=getattr(args,'search_query',None) or ['pump','raydium'],fetched=len(found),age_eligible=eligible_count,scheduled=len(eligible))
                    except Exception as error:
                        discovery['search']=dict(fetch_failed=True,error_type=type(error).__name__)
                        discovery_error=type(error).__name__
            if policy.judgment_profile=='post':
                from .bench import exclude
                before=len(candidates)
                # Keep looking while there is room for another position; tokens already held are excluded above.
                candidates=[] if len(open_tokens)>=desk.rules.max_positions else exclude(desk,candidates)
                discovery['skipped_before_collection']=before-len(candidates)
                discovery['holding_position']=bool(open_tokens);discovery['open_positions']=len(open_tokens)
            world_context=None;entries_blocked=None
            if getattr(args,'worldmonitor_url',None):
                from .worldmonitor import WorldMonitor
                monitor_client=WorldMonitor(args.worldmonitor_url)
                # CHIEF's gate: three green checks, 10 attempts 3s apart. Down means no new entries; RISK continues.
                health=monitor_client.ready_with_retries()
                recorder('worldmonitor:health',health,time.time())
                world_context=dict(health,degraded=not health['ready'])
                if not health['ready']:
                    entries_blocked='worldmonitor_not_ready'
                    discovery_error=discovery_error or 'worldmonitor_not_ready'
            fomo_metrics={}
            if bridge:
                from .fomo import collect_batches
                fomo_metrics,batch_errors=collect_batches(bridge,[c['token'] for c in candidates])
                recorder('fomo:filterTokens',fomo_metrics,time.time())
                discovery['fomo']=dict(status='partial' if batch_errors and fomo_metrics else 'unavailable' if batch_errors else 'complete',returned_tokens=len(fomo_metrics),batch_errors=batch_errors)
                if batch_errors:
                    discovery_error='fomo_batch_failure'
                    recorder('fomo:error',dict(discovery['fomo'],bridge=bridge.status()),time.time())
            if policy.judgment_profile=='post' and bridge:
                surviving=[];free_rejections=[]
                for candidate in candidates:
                    m=fomo_metrics.get(candidate['token']);reasons=[]
                    if not m:reasons=['fomo_evidence_unavailable']
                    else:
                        if m.get('liquidity') is None or m['liquidity']<desk.rules.min_liquidity:reasons.append('liquidity')
                        if m.get('volume_24h') is None or m['volume_24h']<desk.rules.min_volume_24h:reasons.append('volume')
                        if m.get('market_cap') is None or not desk.rules.min_market_cap<=m['market_cap']<=desk.rules.max_market_cap:reasons.append('market_cap')
                        if m.get('created_at') is None or not desk.rules.min_age_hours*3600<=time.time()-m['created_at']<=desk.rules.max_age_hours*3600:reasons.append('age')
                    if reasons:
                        from .bench import reject
                        reject(desk,candidate['token'],reasons)
                        free_rejections.append(dict(token=candidate['token'],reasons=reasons,metrics=m))
                    else:surviving.append(candidate)
                recorder('post:free_cut_rejections',free_rejections,time.time())
                discovery['free_cut']=dict(observed=len(candidates),passed=len(surviving),rejected=len(free_rejections))
                candidates=surviving
            try:
                markets=scanner.batch_pairs([c['token'] for c in candidates]) if hasattr(args,'market_limit') else None
            except Exception as error:
                discovery_error=type(error).__name__
                discovery['market_fetch_failed']=True
                recorder('discovery:market_error',dict(error_type=discovery_error),time.time())
                markets={}
            def turnover(candidate):
                measured=(markets or {}).get(candidate['token'])
                if measured is not None:
                    return -((measured.get('volume_24h') or 0)/max(measured.get('market_cap') or 0,1))
                return -((candidate.get('volume_24h') or 0)/max(candidate.get('market_cap') or 0,1))
            candidates.sort(key=turnover)
            if policy.judgment_profile=='post':
                from .comparison import rank_before_dossiers,first_looks_first
                candidates=rank_before_dossiers(desk,candidates,fomo_metrics,markets or {})
                # Most dossier slots go to tokens never checked before; the rest follow the ranking,
                # so a token waiting on Grok evidence still gets its second look.
                candidates=first_looks_first(desk,candidates,(min(args.limit,policy.max_candidates)+1)//2)
            rpc=(PostSolanaRPC(recorder,http=JsonHTTP(spacing=6.1,attempts=2),allow_mutable_metadata=policy.judgment_profile=='post') if desk.rules.concentration_measure=='token_accounts' else SolanaRPC(recorder,allow_transfer_fees=True))
            jupiter=Jupiter(recorder)
            def read_lock(pool,token):
                """GeckoTerminal's figure and, for PumpSwap pools, the chain's; the lower one counts.
                Neither available means no figure, which the hard rule rejects: a pool is never assumed locked."""
                from .onchain import pumpswap_lock
                found=[]
                for name,read in (('gecko',lambda:gecko.pool_lock(pool)),('chain',lambda:pumpswap_lock(rpc,pool,token))):
                    try:
                        lock=read()
                        if lock and lock.get('locked_pct') is not None:found.append(lock)
                    except Exception as error:recorder('ai:pool_lock_error',dict(token=token,source=name,error_type=type(error).__name__),time.time())
                return min(found,key=lambda l:l['locked_pct']) if found else None
            dossiers=0
            for candidate in candidates:
                token = candidate['token']
                try:
                    snapshot = markets.get(token) if markets is not None else best_pair(scanner.pairs(token))
                    if snapshot is None:
                        recorder('ai:collection_error',dict(token=token,reason='no_dexscreener_base_pair'),time.time())
                        save_filters(desk,run_id,dict(token=token,collection_error='no_dexscreener_base_pair'),time.time())
                        continue
                    if markets is not None and time.time()-snapshot['observed_at']>desk.rules.max_snapshot_age/2:
                        # Earlier dossiers can take minutes; re-fetch this pair rather than vet an aging batch read.
                        snapshot=scanner.pair(snapshot['pair'],token)
                    if bridge:
                        if token not in fomo_metrics:
                            snapshot['early_rejection_reasons']=['fomo_evidence_unavailable']
                        else:
                            snapshot=fomo_enrich(snapshot,fomo_metrics[token])
                    snapshot['discovery'] = dict(source=discovery['source'],
                        pool=candidate['pool'],pool_created_at=candidate['created_at'],first_seen=candidate.get('first_seen'),
                        streams=sorted(streams[token]))
                    if candidate.get('pre_dossier_ranking'):
                        snapshot['pre_dossier_ranking']=candidate['pre_dossier_ranking']
                    # Cheap checks before expensive owner enumeration and quotes.
                    cheap = snapshot.get('early_rejection_reasons',[]) + [r for r in vet(snapshot,desk.rules,time.time()) if r in (
                        'invalid_identity','stale_market_data','missing_price','low_or_missing_liquidity',
                        'pair_age_out_of_range','low_or_missing_volume','market_cap_missing','market_cap_out_of_range',
                        'trade_count_missing','too_few_trades','sell_activity_missing','buys_without_sells','no_recent_pullback')]
                    if cheap:
                        snapshot['early_rejection_reasons'] = cheap
                    if not cheap and dossiers>=min(args.limit,policy.max_candidates):
                        snapshot['early_rejection_reasons']=['dossier_budget_deferred']
                    if not cheap and dossiers<min(args.limit,policy.max_candidates):
                        dossiers+=1
                        evidence = collect(token,desk.rules,rpc,jupiter,check_quotes=policy.judgment_profile!='post',pool=snapshot['pair'])
                        if evidence.get('holders') and fomo_metrics.get(token):
                            evidence['holders']['owner_count']=fomo_metrics[token]['holder_count']
                            evidence['holders']['owner_count_source']='fomo:filterTokens'
                            evidence['holders']['owner_count_observed_at']=fomo_metrics[token]['observed_at']
                        if evidence.get('holders') and desk.rules.concentration_measure=='token_accounts':
                            try:
                                indexed=gecko.token_info(token)
                                snapshot['indexed_dossier']=indexed
                                if policy.judgment_profile=='post':
                                    from .social import request as social_request,requested_at
                                    snapshot['social_evidence']=social_request(desk,token,indexed.get('x_handle'))
                                    snapshot['social_job']=dict(requested_at=requested_at(desk,token))
                                if indexed['holder_count'] is not None:
                                    evidence['holders']['owner_count']=indexed['holder_count']
                                    evidence['holders']['owner_count_source']=indexed['source']
                                    evidence['holders']['owner_count_observed_at']=indexed['observed_at']
                            except Exception as error:
                                evidence['holder_count_error']=type(error).__name__
                        if desk.rules.min_locked_liquidity_pct:
                            snapshot['pool_lock']=read_lock(snapshot['pair'],token)
                        evidence['observed_at']=time.time()
                        evidence['reasons']=preflight_reasons(token,evidence,desk.rules,time.time(),check_quotes=policy.judgment_profile!='post')
                        evidence['screen_passed']=not evidence['reasons']
                        desk.preflight(run_id,evidence)
                        snapshot['preflight'],snapshot['holders'] = evidence,evidence['holders']
                    snapshots.append(snapshot)
                except Exception as error:
                    recorder('ai:collection_error',dict(token=token,error_type=type(error).__name__),time.time())
                    save_filters(desk,run_id,dict(token=token,collection_error='market_collection_failed'),time.time())
            # A dossier may spend time on indexer/RPC work. Refresh the entire quote
            # phase only for candidates whose market and chain evidence still pass.
            for snapshot in snapshots:
                if policy.judgment_profile=='post':continue
                if snapshot.get('early_rejection_reasons'): continue
                evidence=snapshot.get('preflight') or {}
                without_quotes=dict(snapshot,preflight=dict(evidence,quotes=None))
                reasons=vet(without_quotes,desk.rules,time.time())
                quote_prefixes=('buy_','sell_','quote_','roundtrip_','invalid_roundtrip_','invalid_quote_')
                if any(not r.startswith(quote_prefixes) for r in reasons): continue
                try:
                    evidence['quotes']=jupiter.roundtrip(snapshot['token'],desk.rules.ticket,desk.rules.max_quote_slippage_bps)
                except Exception as error:
                    evidence['quotes']=None
                    evidence['errors']=list(dict.fromkeys(evidence.get('errors',[])+['quote_evidence_unavailable']))
                    evidence['quote_error']=type(error).__name__
                evidence['observed_at']=time.time()
                evidence['reasons']=preflight_reasons(snapshot['token'],evidence,desk.rules,time.time())
                evidence['screen_passed']=not evidence['reasons']
                desk.preflight(run_id,evidence)
            if bridge and policy.judgment_profile=='post':
                # Early batches can age out while remaining batches/RPCs complete.
                # Re-fetch real data, never advance a saved observation timestamp.
                refresh_tokens=[s['token'] for s in snapshots if not s.get('early_rejection_reasons')]
                for snapshot in snapshots:
                    if snapshot['token'] not in refresh_tokens:continue
                    try:snapshot.update(market_fields(scanner.pair(snapshot['pair'],snapshot['token'])))
                    except Exception as error:
                        # The old observation stays and ages out under the normal freshness rule.
                        recorder('ai:market_refresh_error',dict(token=snapshot['token'],error_type=type(error).__name__),time.time())
                # No retry wait here: the prices just re-fetched above must not age while waiting on FOMO.
                refreshed,refresh_errors=collect_batches(bridge,refresh_tokens,retry_wait=0)
                recorder('fomo:pre_ai_refresh',dict(metrics=refreshed,errors=refresh_errors),time.time())
                for snapshot in snapshots:
                    if snapshot['token'] not in refresh_tokens:continue
                    if snapshot['token'] in refreshed:
                        snapshot['fomo']=refreshed[snapshot['token']]
                        from .comparison import growth_between
                        # Evidence for the judge only; the hard holder rule keeps using cycle-to-cycle history.
                        snapshot['cycle_growth']=growth_between(fomo_metrics.get(snapshot['token']),refreshed[snapshot['token']])
                    else:
                        snapshot['early_rejection_reasons']=['fomo_evidence_unavailable']
                discovery['fomo']['pre_ai_refresh_errors']=refresh_errors
                if refresh_errors:discovery_error='fomo_refresh_failure'
            if getattr(args,'compare_discovery',False) or getattr(args,'discovery_feed',False):
                snapshots=rank_candidates(desk,run_id,snapshots,check_quotes=policy.judgment_profile!='post')
            def refresh_selected(snapshot):
                fresh=scanner.pair(snapshot['pair'],snapshot['token'])
                # Hold the entry pair fixed and collect new execution evidence after AI latency.
                fresh['discovery']=snapshot.get('discovery')
                if bridge:
                    fresh=fomo_enrich(fresh,bridge.tokens([snapshot['token']])[snapshot['token']])
                evidence=collect(snapshot['token'],desk.rules,rpc,jupiter,pool=snapshot['pair'])
                # Preserve indexed count only while its original timestamp remains fresh.
                old=snapshot.get('holders') or {}
                if evidence.get('holders') and old.get('owner_count_observed_at') and 0<=time.time()-old['owner_count_observed_at']<=desk.rules.max_holder_age:
                    for field in ('owner_count','owner_count_source','owner_count_observed_at'): evidence['holders'][field]=old.get(field)
                evidence['reasons']=preflight_reasons(snapshot['token'],evidence,desk.rules,time.time())
                evidence['screen_passed']=not evidence['reasons']
                desk.preflight(run_id,evidence)
                fresh['preflight'],fresh['holders']=evidence,evidence['holders']
                if desk.rules.min_locked_liquidity_pct:
                    # Read the lock again just before buying: it is the one thing that changes when a pool is about to be emptied.
                    fresh['pool_lock']=read_lock(snapshot['pair'],snapshot['token'])
                return fresh
            sizing=json.loads(Path(args.sizing_file).read_text()) if getattr(args,'sizing_file',None) else {}
            for snapshot in snapshots:
                snapshot['world_context']=world_context
                snapshot['sizing_evidence']=sizing.get(snapshot['token']) if isinstance(sizing,dict) and not getattr(args,'observe_only',False) else None
                snapshot['observe_only']=getattr(args,'observe_only',False)
                snapshot['entries_blocked']=entries_blocked
            result = evaluate(desk,run_id,snapshots,AIProviders(recorder,policy),policy,refresh_selected=refresh_selected)
            discovery['market_observations']=len(snapshots)
            discovery['market_passed']=sum(not any(r in ('invalid_identity','stale_market_data','missing_price','low_or_missing_liquidity','pair_age_out_of_range','low_or_missing_volume','market_cap_missing','market_cap_out_of_range','trade_count_missing','too_few_trades','sell_activity_missing','buys_without_sells') for r in s.get('early_rejection_reasons',[])) for s in snapshots)
            discovery['dossiers_collected']=dossiers
            discovery['dossiers_deferred']=sum('dossier_budget_deferred' in s.get('early_rejection_reasons',[]) for s in snapshots)
            result['discovery'] = discovery
            if discovery_error and result['state']=='complete':
                result['state'] = 'degraded'
            if policy.judgment_profile=='post':
                from .reporting import after_cycle
                result['telegram']=after_cycle(desk,result,run_id=run_id,snapshots=snapshots)
            with desk.db:
                desk.db.execute('UPDATE ai_cycles SET state=?,summary=? WHERE run_id=?',(result['state'],encode(result),run_id))
            if args.command=='ai-scan':
                return result
            print(json.dumps(result),flush=True)
            time.sleep(max(0,args.interval-(time.monotonic()-start)))
    finally:
        if bridge: bridge.close()
