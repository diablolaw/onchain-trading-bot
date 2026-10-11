"""SPL mint checks and complete, conservative owner aggregation."""
import os
from collections import defaultdict
from decimal import Decimal

from .http import JsonHTTP

TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022_PROGRAM = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
class UnsupportedTokenFeature(ValueError):
    """Returned chain data uses a feature our validator cannot support."""


class MetadataIdentityMismatch(ValueError):
    """Metadata references a different mint or external metadata account."""


ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def address(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        raise ValueError('invalid Solana address')
    total = 0
    try:
        for char in value:
            total = total*58 + ALPHABET.index(char)
    except ValueError:
        raise ValueError('invalid Solana address') from None
    size = (total.bit_length()+7)//8 + len(value)-len(value.lstrip('1'))
    if size != 32:
        raise ValueError('Solana public key must decode to 32 bytes')
    return value


def uint(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit():
        raise ValueError('invalid unsigned integer')
    result = int(value)
    if result > 2**64-1:
        raise ValueError('unsigned integer exceeds u64 range')
    return result


def slot(result):
    return uint(result['context']['slot'])


def parsed_account(account, kind, program=None):
    if (not isinstance(account, dict) or account.get('owner') not in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM)
            or (program is not None and account.get('owner') != program) or account.get('executable') is not False):
        raise ValueError('unsupported token program or executable account')
    parsed = account['data']['parsed']
    if parsed['type'] != kind:
        raise ValueError('unexpected parsed token account type')
    return parsed['info']


def transfer_fee_config(state):
    for field in ('transferFeeConfigAuthority','withdrawWithheldAuthority'):
        if field not in state: raise ValueError('missing transfer fee authority evidence')
        if state[field] is not None: address(state[field])
    uint(state['withheldAmount'])
    for key in ('olderTransferFee','newerTransferFee'):
        fee=state[key]
        uint(fee['epoch']); uint(fee['maximumFee'])
        if uint(fee['transferFeeBasisPoints'])>10000: raise ValueError('invalid transfer fee basis points')
    if uint(state['olderTransferFee']['epoch'])>uint(state['newerTransferFee']['epoch']):
        raise ValueError('invalid transfer fee epoch order')
    return state


def extensions(account, kind, token, allow_transfer_fees=False, allow_mutable_metadata=False):
    info = parsed_account(account, kind)
    items = info.get('extensions', [])
    if not isinstance(items, list):
        raise ValueError('invalid token extension evidence')
    if account['owner'] == TOKEN_PROGRAM:
        if items:
            raise ValueError('unexpected extensions on classic token')
        return []
    # An absent extension list is valid only for the base-sized account.
    if 'extensions' not in info and account['data'].get('space') != (82 if kind == 'mint' else 165):
        raise ValueError('missing Token-2022 extension evidence')
    allowed = {'metadataPointer', 'tokenMetadata'} if kind == 'mint' else {'immutableOwner'}
    if allow_transfer_fees:
        allowed = allowed | ({'transferFeeConfig'} if kind=='mint' else {'transferFeeAmount'})
    names = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('invalid token extension descriptor')
        name = item.get('extension')
        if name not in allowed or name in names:
            raise UnsupportedTokenFeature('unsupported Token-2022 extension: ' + str(name))
        names.append(name)
        state = item.get('state') or {}
        if not isinstance(state, dict):
            raise ValueError('invalid token extension state')
        if name=='transferFeeConfig': transfer_fee_config(state)
        if name=='transferFeeAmount': uint(state['withheldAmount'])
        if name in ('metadataPointer','tokenMetadata'):
            identity, authority = ('metadataAddress','authority') if name=='metadataPointer' else ('mint','updateAuthority')
            if identity not in state or authority not in state:
                raise ValueError('missing '+name+' identity or authority evidence')
            if state[identity] != token:
                raise MetadataIdentityMismatch(name+' identity mismatch: expected current mint')
            if state[authority] is not None:
                address(state[authority])
                if not allow_mutable_metadata:
                    raise UnsupportedTokenFeature('mutable '+name+' is unsupported: '+authority+' is active')
    return names


def mint_evidence(token, result, observed_at, allow_transfer_fees=False, allow_mutable_metadata=False):
    address(token)
    account = result.get('value')
    info = parsed_account(account, 'mint')
    extension_names = extensions(account, 'mint', token, allow_transfer_fees, allow_mutable_metadata)
    supply = uint(info['supply'])
    decimals = uint(info['decimals'])
    if supply <= 0 or decimals > 18 or info.get('isInitialized') is not True:
        raise ValueError('uninitialized mint, unsupported decimals or empty supply')
    # Require explicit null, never infer revocation from an absent field.
    for field in ('mintAuthority', 'freezeAuthority'):
        if field not in info:
            raise ValueError('missing mint authority evidence')
        if info[field] is not None:
            address(info[field])
    fee=next((item['state'] for item in info.get('extensions',[]) if item.get('extension')=='transferFeeConfig'),None)
    return dict(token=token, source='solana_rpc', program=account['owner'], extensions=extension_names,
                transfer_fee=fee,
                metadata_authorities={item['extension']:item['state'].get('authority' if item['extension']=='metadataPointer' else 'updateAuthority') for item in info.get('extensions',[]) if item.get('extension') in ('metadataPointer','tokenMetadata')},
                observed_at=observed_at, slot=slot(result), supply=str(supply), decimals=decimals,
                mint_authority=info['mintAuthority'], freeze_authority=info['freezeAuthority'],
                supported_features=True)


def owner_evidence(token, mint, result, observed_at, max_accounts=10000):
    accounts = result['value']
    if not isinstance(accounts, list) or not accounts or len(accounts) > max_accounts:
        raise ValueError('empty or oversized holder account enumeration')
    owners, seen = defaultdict(int), set()
    token_balances=[]
    withheld=uint((mint.get('transfer_fee') or {}).get('withheldAmount',0))
    supply = uint(mint['supply'])
    holder_slot = slot(result)
    if holder_slot < mint['slot']:
        raise ValueError('holder enumeration predates mint evidence')
    for row in accounts:
        public_key = address(row['pubkey'])
        if public_key in seen:
            raise ValueError('duplicate holder account')
        seen.add(public_key)
        info = parsed_account(row['account'], 'account', mint['program'])
        extensions(row['account'], 'account', token, bool(mint.get('transfer_fee')))
        for item in info.get('extensions',[]):
            if item.get('extension')=='transferFeeAmount': withheld+=uint(item['state']['withheldAmount'])
        if info['mint'] != token or info.get('state') != 'initialized':
            raise ValueError('wrong mint or frozen/uninitialized holder account')
        owner = address(info['owner'])
        amount = uint(info['tokenAmount']['amount'])
        if uint(info['tokenAmount']['decimals']) != mint['decimals']:
            raise ValueError('holder decimals mismatch')
        owners[owner] += amount
        token_balances.append(dict(account=public_key,owner=owner,amount=str(amount)))
    # Reject truncated results and supply changes between the two requests.
    # Equality cannot prove a malicious provider is honest; RPC is trusted evidence.
    if sum(owners.values())+withheld != supply:
        raise ValueError('holder balances do not reconcile with mint supply')
    token_balances.sort(key=lambda item:int(item['amount']),reverse=True)
    balances = sorted((amount for amount in owners.values() if amount > 0), reverse=True)
    if not balances:
        raise ValueError('no funded owners')
    return dict(token=token, observed_at=observed_at, source='solana_rpc:all_token_accounts:no_exclusions',
                method='owner_aggregated', slot=holder_slot, mint_slot=mint['slot'],
                supply=str(supply), accounted_supply=str(sum(owners.values())+withheld),withheld_supply=str(withheld),
                top_accounts=token_balances[:20],
                top_account_pct=float(Decimal(int(token_balances[0]['amount']))*100/Decimal(supply)),
                top10_account_pct=float(Decimal(sum(int(item['amount']) for item in token_balances[:10]))*100/Decimal(supply)),
                account_count=len(accounts), owner_count=len(balances), exclusions=[],
                top_owner_pct=float(Decimal(balances[0])*100/Decimal(supply)),
                top10_owner_pct=float(Decimal(sum(balances[:10]))*100/Decimal(supply)))


class SolanaRPC:
    def __init__(self, recorder, http=None, url=None, allow_transfer_fees=False, allow_mutable_metadata=False):
        self.allow_transfer_fees=allow_transfer_fees
        self.allow_mutable_metadata=allow_mutable_metadata
        self.recorder = recorder
        self.http = http or JsonHTTP(spacing=1.1)
        self.url = url or os.environ.get('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')

    def call(self, method, params):
        if method not in ('getAccountInfo', 'getProgramAccounts'):
            raise ValueError('RPC method not permitted by read-only preflight')
        data, stamp = self.http.request(self.url, payload=dict(jsonrpc='2.0', id=1, method=method, params=params))
        self.recorder('solana:'+method, data, stamp)
        if not isinstance(data, dict) or data.get('error') or data.get('id') != 1 or 'result' not in data:
            raise ValueError('RPC error or invalid response; check provider support')
        return data['result'], stamp

    def inspect(self, token):
        address(token)
        result, stamp = self.call('getAccountInfo', [token, {'encoding':'jsonParsed','commitment':'confirmed'}])
        mint = mint_evidence(token, result, stamp,self.allow_transfer_fees)
        # Avoid expensive enumeration of already-ineligible authority-bearing mints.
        if mint['mint_authority'] is not None or mint['freeze_authority'] is not None:
            return mint, None
        filters = [{'memcmp':{'offset':0,'bytes':token}}]
        if mint['program'] == TOKEN_PROGRAM:
            filters.insert(0, {'dataSize':165})
        result, stamp = self.call('getProgramAccounts', [mint['program'],
            {'encoding':'jsonParsed', 'commitment':'confirmed','withContext':True,
             'minContextSlot':mint['slot'],
             'filters':filters}])
        return mint, owner_evidence(token, mint, result, stamp)


# Shared AMM vault authorities whose token accounts hold pool reserves, not a trader's bag.
# Pools that own their vaults directly (PumpSwap, Raydium CLMM, Orca, Meteora DLMM) are matched
# by the traded pair address instead. Anything not identified stays in the measurement.
POOL_AUTHORITIES = {
    '5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1': 'raydium_amm_v4_authority',
    'GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL': 'raydium_cpmm_authority',
}


class PostSolanaRPC(SolanaRPC):
    """Guide's largest-token-account concentration, with identified pool vaults labelled and excluded."""
    pool_owners = POOL_AUTHORITIES
    batch_accounts = True

    def call(self, method, params):
        if method not in ('getTokenLargestAccounts','getTokenSupply','getMultipleAccounts'):
            return super().call(method,params)
        data,stamp=self.http.request(self.url,payload=dict(jsonrpc='2.0',id=1,method=method,params=params))
        self.recorder('solana:'+method,data,stamp)
        if not isinstance(data,dict) or data.get('error') or data.get('id')!=1 or 'result' not in data:
            raise ValueError('RPC error or invalid response; check provider support')
        return data['result'],stamp

    def inspect(self,token,pool=None):
        result,stamp=self.call('getAccountInfo',[address(token),{'encoding':'jsonParsed','commitment':'confirmed'}])
        mint=mint_evidence(token,result,stamp,allow_transfer_fees=True,allow_mutable_metadata=self.allow_mutable_metadata)
        self.last_mint=mint
        if mint['mint_authority'] is not None or mint['freeze_authority'] is not None:
            return mint,None
        supply_result,_=self.call('getTokenSupply',[token,{'commitment':'confirmed','minContextSlot':mint['slot']}])
        supply=uint(supply_result['value']['amount'])
        if supply!=uint(mint['supply']) or uint(supply_result['value']['decimals'])!=mint['decimals'] or slot(supply_result)<mint['slot']:
            raise ValueError('mint and token supply evidence mismatch')
        largest,stamp=self.call('getTokenLargestAccounts',[token,{'commitment':'confirmed'}])
        holders=largest_account_evidence(token,mint,largest,stamp)
        if pool:
            pool=address(pool);accounts=[row['account'] for row in holders['top_accounts'][:20]]
            def enough(rows):
                # Concentration only reads the top 10 non-vault accounts; owners past that change nothing.
                return sum(1 for row in rows if account_owner(row) not in (pool,*self.pool_owners))>=10
            owners=self.accounts(accounts,enough)
            holders=exclude_pool_vaults(holders,mint,owners,pool,self.pool_owners)
        return mint,holders

    def accounts(self,accounts,enough=None):
        """getMultipleAccounts, or one getAccountInfo per account when the provider refuses the batch."""
        if self.batch_accounts:
            try:
                result,_=self.call('getMultipleAccounts',[accounts,{'encoding':'jsonParsed','commitment':'confirmed'}])
                return result
            except RuntimeError as error:
                if str(error)!='provider HTTP 413':raise
                self.batch_accounts=False  # Some plans (seen on QuickNode) return 413 for this method.
        rows=[]
        for account in accounts:
            if enough and enough(rows):break
            result,_=self.call('getAccountInfo',[account,{'encoding':'jsonParsed','commitment':'confirmed'}])
            rows.append(result.get('value') if isinstance(result,dict) else None)
        return dict(value=rows+[None]*(len(accounts)-len(rows)))

    def owners(self,token,accounts):
        """Owner of each token account, for RISK's whale-distribution check."""
        return token_account_owners(token,accounts,self.accounts(accounts))


def account_owner(row):
    try:return row['data']['parsed']['info']['owner']
    except (KeyError,TypeError):return None


def token_account_owners(token,accounts,result):
    rows=result.get('value') if isinstance(result,dict) else None
    if not isinstance(rows,list) or len(rows)!=len(accounts):
        raise ValueError('invalid token account owner evidence')
    owners={}
    for account,row in zip(accounts,rows):
        if row is None:continue
        info=parsed_account(row,'account')
        if info.get('mint')!=token:raise ValueError('token account mint mismatch')
        owners[account]=address(info['owner'])
    return owners


def exclude_pool_vaults(holders,mint,result,pool,authorities):
    """Recompute concentration without token accounts owned by the traded pool or a known AMM authority.

    Raw figures stay in the evidence. An account whose owner cannot be read is kept, never guessed."""
    accounts=[row['account'] for row in holders['top_accounts'][:20]]
    owners=token_account_owners(holders['token'],accounts,result)
    supply=Decimal(uint(mint['supply']));kept=[];exclusions=[]
    for row in holders['top_accounts']:
        owner=owners.get(row['account'])
        label='traded_pool' if owner==pool else authorities.get(owner)
        row=dict(row,owner=owner)
        if label:exclusions.append(dict(row,reason=label))
        else:kept.append(row)
    if not kept:raise ValueError('no non-vault holder accounts in largest-account evidence')
    return dict(holders,top_accounts=kept,exclusions=exclusions,pool=pool,
        source=holders['source']+'+getMultipleAccounts:vault_exclusion',
        raw_top_account_pct=holders['top_account_pct'],raw_top10_account_pct=holders['top10_account_pct'],
        top_account_pct=float(Decimal(int(kept[0]['amount']))*100/supply),
        top10_account_pct=float(Decimal(sum(int(r['amount']) for r in kept[:10]))*100/supply))


def largest_account_evidence(token,mint,result,stamp):
    rows=result['value'];supply=uint(mint['supply'])
    # Some providers expand the standard top-20 response to top-100.
    # Validate every returned account; concentration still uses the largest ten.
    if not isinstance(rows,list) or not 1<=len(rows)<=100 or slot(result)<mint['slot']:
        raise ValueError('invalid largest-token-account evidence')
    balances=[];seen=set()
    for row in rows:
        account=address(row['address']);amount=uint(row['amount'])
        if account in seen or uint(row['decimals'])!=mint['decimals']:
            raise ValueError('duplicate account or decimals mismatch')
        seen.add(account);balances.append(dict(account=account,amount=str(amount)))
    balances.sort(key=lambda r:int(r['amount']),reverse=True)
    if sum(int(r['amount']) for r in balances)>supply or not int(balances[0]['amount']):
        raise ValueError('largest account balances exceed supply or are empty')
    return dict(token=token,observed_at=stamp,source='solana_rpc:getTokenLargestAccounts/getTokenSupply',
        method='token_accounts',slot=slot(result),mint_slot=mint['slot'],supply=str(supply),
        supply_verified=True,owner_count=None,owner_count_source=None,top_accounts=balances,exclusions=[],
        top_account_pct=float(Decimal(int(balances[0]['amount']))*100/Decimal(supply)),
        top10_account_pct=float(Decimal(sum(int(r['amount']) for r in balances[:10]))*100/Decimal(supply)))
