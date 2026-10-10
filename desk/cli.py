import argparse
import json
import logging
import time
from dataclasses import replace
from pathlib import Path

from .core import Desk, Rules, normalize
from .scanner import DexScreener, best_pair
from .onchain import SolanaRPC, PostSolanaRPC, address
from .preflight import collect, quote_reasons
from .quotes import Jupiter
from . import live_cli, ai_cli
from .env import load_env

LOG = logging.getLogger('desk')


def fixture(desk, path):
    events = json.loads(Path(path).read_text())
    run_id = desk.run('replay', time.time())
    for event in events:
        stamp = event['observed_at']
        desk.raw(run_id, 'fixture', event, stamp)
        snapshot = normalize(event['pair'], stamp, event.get('holders'))
        if 'preflight' in event:
            snapshot['preflight'] = event['preflight']
        desk.record(run_id, snapshot, stamp)
    return desk.report(events[-1]['observed_at'] if events else time.time())


def monitor(desk, scanner, run_id, rpc=None):
    # Always pinned to the entry pair; liquidity-based pair selection cannot
    # switch the price used to value an existing position.
    for position in desk.open_positions():
        whale = None
        if rpc is not None and desk.rules.whale_hold_max_seconds:
            from .whale import check as whale_check
            try:
                whale = whale_check(desk, rpc, position['token'], position['pair'])
            except Exception as error:
                # Missing whale evidence never delays an exit.
                desk.raw(run_id, 'risk_whale_error', {'pair': position['pair'], 'error_type': type(error).__name__}, time.time())
        attempts=3 if desk.rules.exit_policy=='post_volume' else 1
        for attempt in range(attempts):
            try:
                snapshot = scanner.pair(position['pair'], position['token'])
                pending=desk.db.execute('SELECT 1 FROM risk_close_intents WHERE pair=?',(position['pair'],)).fetchone()
                if desk.rules.exit_policy=='post_volume' and (snapshot.get('price') is None or (not pending and (snapshot.get('volume_6h') is None or snapshot.get('volume_24h') is None))):
                    raise ValueError('incomplete post exit evidence')
                if whale is not None:
                    snapshot['whale'] = whale
                desk.record(run_id, snapshot, time.time(), enter=False)
                break
            except Exception as error:
                now = time.time()
                desk.raw(run_id, 'risk_error', {'pair': position['pair'], 'error_type': type(error).__name__, 'attempt':attempt+1}, now)
                if attempt+1<attempts:continue
                with desk.db:
                    desk.log(run_id, None, now, 'RISK', 'blocked', ['market_fetch_failed:' + position['pair']])
                    if desk.rules.exit_policy=='post_volume':
                        desk.db.execute('INSERT OR IGNORE INTO risk_close_intents VALUES (?,?,?)',(position['pair'],now,'exit_evidence_failed_after_two_retries'))
                LOG.error('Risk data unavailable for %s after %s attempts',position['pair'],attempts)


def live_cycle(desk, kind, limit, holder_path):
    run_id = desk.run(kind, time.time())
    scanner = DexScreener(lambda source, data, stamp: desk.raw(run_id, source, data, stamp))
    recorder = lambda source, data, stamp: desk.raw(run_id, source, data, stamp)
    rpc, jupiter = SolanaRPC(recorder), Jupiter(recorder)
    whale_rpc = PostSolanaRPC(recorder) if desk.rules.whale_hold_max_seconds else None
    monitor(desk, scanner, run_id, whale_rpc)
    if kind == 'risk':
        return
    evidence = json.loads(Path(holder_path).read_text()) if holder_path else {}
    tokens = scanner.discover(limit)
    # Refresh existing watched tokens, even when absent from latest profiles.
    watched = desk.db.execute('SELECT DISTINCT token FROM snapshots ORDER BY token LIMIT 100').fetchall()
    tokens = sorted(set(tokens) | {row[0] for row in watched})
    open_tokens = {p['token'] for p in desk.open_positions()}
    for token in tokens:
        if token in open_tokens:
            continue
        try:
            s = best_pair(scanner.pairs(token))
            if s:
                if desk.rules.require_preflight:
                    preflight = collect(token, desk.rules, rpc, jupiter)
                    desk.preflight(run_id, preflight)
                    s['preflight'] = preflight
                    s['holders'] = preflight['holders']
                else:
                    s['holders'] = evidence.get(token)
                desk.record(run_id, s, time.time())
        except Exception as error:
            desk.raw(run_id, 'scan_error', {'token': token, 'error': str(error)}, time.time())
            LOG.error('Could not scan %s: %s', token, error)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Solana scanner and auditable paper trading desk')
    parser.add_argument('--db', default='data/desk.sqlite')
    parser.add_argument('--config', help='JSON rule overrides')
    parser.add_argument('--preflight', action='store_true', help='Require automatic on-chain and quote evidence for paper entries')
    sub = parser.add_subparsers(dest='command', required=True)
    scan = sub.add_parser('scan', help='One discovery, vetting and paper entry cycle')
    scan.add_argument('--limit', type=int, default=20)
    scan.add_argument('--holders', help='JSON owner-level evidence keyed by mint')
    risk = sub.add_parser('risk', help='Independent paper exit worker')
    risk.add_argument('--interval', type=float, default=15)
    risk.add_argument('--once', action='store_true')
    watch = sub.add_parser('watch', help='Continuous discovery worker (run risk separately)')
    watch.add_argument('--interval', type=float, default=300)
    watch.add_argument('--limit', type=int, default=20)
    watch.add_argument('--holders')
    replay = sub.add_parser('replay', help='Replay timestamped fixture into SQLite')
    replay.add_argument('path')
    sub.add_parser('report')
    sub.add_parser('decisions')
    for name in ('check', 'quotes'):
        command = sub.add_parser(name, help='Read-only on-chain and quote screening' if name == 'check' else 'Buy/sell quote screening only')
        command.add_argument('mint')
        command.add_argument('--amount-usdc', type=float, help='USDC ticket to quote (defaults to configured ticket)')
    live_cli.add_commands(parser, sub)
    ai_cli.add_commands(parser, sub)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        load_env()
        rules = Rules(**json.loads(Path(args.config).read_text())) if args.config else Rules()
        if args.preflight:
            rules = replace(rules, require_preflight=True)
        if args.command in ('ai-scan','ai-watch'):
            rules = replace(rules, require_preflight=True)
        if getattr(args, 'amount_usdc', None) is not None:
            rules = replace(rules, ticket=args.amount_usdc)
        if getattr(args, 'interval', 1) < 1 or getattr(args, 'limit', 20) not in range(1, 101):
            raise ValueError('interval must be >=1 and limit must be 1..100')
        desk = Desk(args.db, rules)
        try:
            if args.command in live_cli.COMMANDS:
                print(json.dumps(live_cli.run(args, desk), indent=2))
            elif args.command in ai_cli.COMMANDS:
                result = ai_cli.run(args,desk)
                print(result if isinstance(result,str) else json.dumps(result,indent=2))
                if isinstance(result,dict) and result.get('state')=='blocked':
                    return 2
            elif args.command == 'report':
                print(json.dumps(desk.report(), indent=2))
            elif args.command == 'decisions':
                for row in desk.db.execute('SELECT * FROM decisions ORDER BY id'):
                    print(json.dumps(dict(row)))
            elif args.command == 'replay':
                print(json.dumps(fixture(desk, args.path), indent=2))
            elif args.command in ('check', 'quotes'):
                address(args.mint)
                run_id = desk.run(args.command, time.time())
                recorder = lambda source, data, stamp: desk.raw(run_id, source, data, stamp)
                jupiter = Jupiter(recorder)
                if args.command == 'check':
                    result = collect(args.mint, rules, SolanaRPC(recorder), jupiter)
                else:
                    result = dict(token=args.mint, quotes=None, errors=[], ready_for_live=False)
                    try:
                        result['quotes'] = jupiter.roundtrip(args.mint, rules.ticket, rules.max_quote_slippage_bps)
                    except (ValueError, RuntimeError, KeyError, TypeError) as error:
                        result['errors'].append('quote_evidence_unavailable')
                        result['quote_error'] = str(error)
                    result['observed_at'] = time.time()
                    result['reasons'] = result['errors'] + quote_reasons(args.mint, result['quotes'], rules, result['observed_at'])
                    result['screen_passed'] = not result['reasons']
                result['id'] = desk.preflight(run_id, result)
                print(json.dumps(result, indent=2))
                return 0 if result['screen_passed'] else 2
            else:
                while True:
                    start = time.monotonic()
                    kind = 'risk' if args.command == 'risk' else 'scan'
                    try:
                        live_cycle(desk, kind, getattr(args, 'limit', 20), getattr(args, 'holders', None))
                    except Exception as error:
                        if args.command == 'scan' or getattr(args, 'once', False):
                            raise
                        LOG.error('Cycle failed: %s', error)
                    print(json.dumps(desk.report()), flush=True)
                    if args.command == 'scan' or getattr(args, 'once', False):
                        break
                    time.sleep(max(0, args.interval - (time.monotonic() - start)))
        finally:
            desk.close()
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        LOG.error('%s', error)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
