"""Live execution RPC: submission is isolated from retryable reads and simulation."""
import base64
import os

from .http import JsonHTTP
from .onchain import TOKEN_PROGRAM, TOKEN_2022_PROGRAM, address, uint
from .quotes import WRAPPED_SOL
from .wallet import sdk

SYSTEM = '11111111111111111111111111111111'
ATA_PROGRAM = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'


def ata(wallet, mint, program):
    _, Pubkey, _, _ = sdk()
    return str(Pubkey.find_program_address([bytes(Pubkey.from_string(wallet)),
        bytes(Pubkey.from_string(program)), bytes(Pubkey.from_string(mint))], Pubkey.from_string(ATA_PROGRAM))[0])


def raw_data(account):
    if account is None:
        return b''
    data = account['data']
    if not isinstance(data, list) or len(data) != 2 or data[1] != 'base64':
        raise ValueError('expected base64 RPC account data')
    return base64.b64decode(data[0], validate=True)


def token_account(account, owner=None, mint=None):
    if account is None:
        return None
    if account['owner'] not in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM) or account.get('executable') is not False:
        raise ValueError('invalid wallet token account program')
    data = raw_data(account)
    if len(data) < 165 or data[108] != 1:
        raise ValueError('wallet token account is frozen or malformed')
    if int.from_bytes(data[72:76],'little') != 0 or int.from_bytes(data[129:133],'little') != 0:
        raise ValueError('pilot wallet token accounts must not have delegate or close authority')
    _, Pubkey, _, _ = sdk()
    result = dict(mint=str(Pubkey.from_bytes(data[:32])), owner=str(Pubkey.from_bytes(data[32:64])),
                  amount=int.from_bytes(data[64:72], 'little'), program=account['owner'])
    native = int.from_bytes(data[109:113], 'little')
    if native not in (0,1) or (result['mint'] == WRAPPED_SOL) != (native == 1):
        raise ValueError('native token account flag does not match canonical wrapped SOL mint')
    if native and uint(account['lamports']) != int.from_bytes(data[113:121], 'little')+result['amount']:
        raise ValueError('wrapped SOL balance does not reconcile with backing lamports')
    if owner is not None and result['owner'] != owner or mint is not None and result['mint'] != mint:
        raise ValueError('wallet token account identity mismatch')
    return result


class LiveRPC:
    read_methods = {'getAccountInfo','getMultipleAccounts','getTokenAccountsByOwner',
                    'simulateTransaction','getFeeForMessage','getSignatureStatuses','getTransaction',
                    'getBlockHeight','isBlockhashValid','getGenesisHash'}

    def __init__(self, recorder, http=None, url=None, submit_http=None):
        self.recorder = recorder
        self.http = http or JsonHTTP(spacing=1.1)
        self.submit_http = submit_http or JsonHTTP(attempts=1, spacing=0)
        self.url = url or os.environ.get('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')

    def _call(self, method, params, transport):
        data, stamp = transport.request(self.url, payload=dict(jsonrpc='2.0', id=1, method=method, params=params))
        self.recorder('solana_live:'+method, data, stamp)
        if not isinstance(data, dict) or data.get('error') or data.get('id') != 1 or 'result' not in data:
            raise RuntimeError('live RPC returned an error or invalid response')
        return data['result']

    def call(self, method, params):
        if method not in self.read_methods:
            raise ValueError('method is not a live read or simulation')
        return self._call(method, params, self.http)

    def mainnet(self):
        if self.call('getGenesisHash', []) != '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d':
            raise ValueError('live pilot requires Solana mainnet-beta RPC')

    def snapshot(self, wallet, extra_accounts=()):
        address(wallet)
        keys = {wallet, *extra_accounts}
        context_slot = 0
        for program in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
            result = self.call('getTokenAccountsByOwner', [wallet, {'programId':program},
                              {'encoding':'base64','commitment':'confirmed'}])
            context_slot = max(context_slot, uint(result['context']['slot']))
            for row in result['value']:
                address(row['pubkey'])
                token_account(row['account'], wallet)
                keys.add(row['pubkey'])
        if len(keys) > 20:
            raise ValueError('isolated pilot wallet may have at most 19 token accounts')
        keys = [wallet] + sorted(keys-{wallet})
        result = self.call('getMultipleAccounts', [keys,
            {'encoding':'base64','commitment':'confirmed','minContextSlot':context_slot}])
        if len(result['value']) != len(keys):
            raise ValueError('wallet account snapshot was truncated')
        accounts = dict(zip(keys, result['value']))
        native = accounts[wallet]
        if native is not None and (native['owner'] != SYSTEM or native.get('executable') is not False or raw_data(native)):
            raise ValueError('wallet must be a plain system-owned account')
        balances = {}
        for key in keys[1:]:
            item = token_account(accounts[key], wallet)
            if item:
                balances[item['mint']] = balances.get(item['mint'], 0)+item['amount']
        return dict(wallet=wallet, slot=uint(result['context']['slot']), accounts=accounts,
                    balances=balances, sol_lamports=uint(native['lamports']) if native else 0)

    def simulate(self, transaction, keys, verify=False, min_slot=0):
        result = self.call('simulateTransaction', [transaction,
            {'encoding':'base64','commitment':'confirmed','sigVerify':verify,
             'replaceRecentBlockhash':False,'minContextSlot':min_slot,
             'accounts':{'encoding':'base64','addresses':keys}}])
        if result['value'].get('err') is not None:
            raise ValueError('transaction simulation failed')
        if uint(result['context']['slot']) < min_slot:
            raise ValueError('simulation predates wallet snapshot')
        values = result['value'].get('accounts')
        if not isinstance(values, list) or len(values) != len(keys):
            raise ValueError('simulation omitted protected wallet accounts')
        return dict(slot=uint(result['context']['slot']), accounts=dict(zip(keys, values)),
                    units_consumed=result['value'].get('unitsConsumed'))

    def send(self, signed_transaction):
        # A timeout means UNKNOWN. Never retry a money-changing request in transport.
        return self._call('sendTransaction', [signed_transaction,
            {'encoding':'base64','skipPreflight':False,'preflightCommitment':'confirmed','maxRetries':0}], self.submit_http)
