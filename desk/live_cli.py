"""Explicit wallet and manual execution commands; scanner never invokes submission."""
import json
import time
from pathlib import Path

from .live import LiveBook, LiveEngine, LiveLimits
from .live_jupiter import LiveJupiter
from .live_rpc import LiveRPC
from .onchain import SolanaRPC
from .quotes import Jupiter, USDC
from .scanner import DexScreener
from .wallet import create_wallet, load_wallet


def add_commands(parser, sub):
    parser.add_argument('--wallet',default='data/live-wallet.json',help='Owner-only Solana CLI keypair file for manual live tools')
    parser.add_argument('--live-config',help='JSON live risk limits (defaults match live.config.json)')
    sub.add_parser('wallet-create',help='Create a new unfunded isolated wallet; never overwrites')
    sub.add_parser('wallet-info',help='Display public address only')
    sub.add_parser('wallet-status',help='Read mainnet balances and funding limits')
    sub.add_parser('live-status',help='Display live order journal and live positions')
    kill = sub.add_parser('live-kill-switch',help='Block new live entries; exits remain permitted')
    kill.add_argument('state',choices=['on','off'])
    prepare = sub.add_parser('live-prepare',help='Vet, build, inspect and simulate without signing or broadcasting')
    prepare.add_argument('mint')
    prepare.add_argument('--side',choices=['buy','sell'],default='buy')
    prepare.add_argument('--execution-test',action='store_true',help='Canonical wrapped SOL only: test execution mechanics independently of memecoin entry rules')
    review = sub.add_parser('live-review',help='Review a stored order without changing it')
    review.add_argument('order_id')
    cancel = sub.add_parser('live-cancel',help='Cancel only an intent with no persisted transaction signature')
    cancel.add_argument('order_id')
    submit = sub.add_parser('live-submit',help='Sign and broadcast exactly one reviewed order')
    submit.add_argument('order_id')
    submit.add_argument('--approve',required=True,help='Exact SHA-256 approval hash displayed by live-prepare')
    recover = sub.add_parser('live-recover',help='Read finalized on-chain state and reconcile; never broadcasts')
    recover.add_argument('order_id')


COMMANDS = {'wallet-create','wallet-info','wallet-status','live-status','live-kill-switch',
            'live-prepare','live-review','live-cancel','live-submit','live-recover'}


def run(args, desk):
    if args.command == 'wallet-create':
        return create_wallet(args.wallet)
    signer = load_wallet(args.wallet)
    wallet = str(signer.pubkey())
    if args.command == 'wallet-info':
        return dict(address=wallet)
    limits = LiveLimits(**json.loads(Path(args.live_config).read_text())) if args.live_config else LiveLimits()
    book = LiveBook(desk,wallet,limits)
    run_id = desk.run(args.command,time.time())
    recorder = lambda source,data,stamp:desk.raw(run_id,source,data,stamp)
    rpc = LiveRPC(recorder)
    if args.command == 'wallet-status':
        rpc.mainnet()
        snapshot = rpc.snapshot(wallet)
        return dict(wallet=wallet,sol_lamports=snapshot['sol_lamports'],usdc_atomic=snapshot['balances'].get(USDC,0),
                    token_balances=snapshot['balances'],minimum_usdc_for_ticket=limits.ticket_usdc,
                    minimum_sol_lamports_for_buy_and_exit=limits.min_sol_reserve_lamports+2*limits.max_network_lamports,
                    max_wallet_usdc=limits.max_wallet_usdc,max_wallet_sol_lamports=limits.max_wallet_sol_lamports,
                    kill_switch=book.status()['kill_switch'])
    if args.command == 'live-kill-switch':
        book.kill(args.state == 'on')
        return dict(kill_switch=args.state)
    if args.command == 'live-status':
        return book.status()
    if args.command == 'live-cancel':
        book.cancel(args.order_id)
        return dict(order_id=args.order_id,state='cancelled')
    engine = LiveEngine(book,signer,rpc,LiveJupiter(recorder),DexScreener(recorder),SolanaRPC(recorder),Jupiter(recorder))
    if args.command == 'live-prepare':
        return engine.prepare(args.mint,args.side,args.execution_test)
    if args.command == 'live-review':
        return engine.review(args.order_id)
    if args.command == 'live-submit':
        return engine.submit(args.order_id,args.approve)
    return engine.recover(args.order_id)
