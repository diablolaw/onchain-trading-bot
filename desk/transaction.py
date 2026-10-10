"""Fail-closed inspection of narrowly supported Jupiter V6 ExactIn transactions."""
import base64
import hashlib
import struct

from .live_rpc import ATA_PROGRAM, SYSTEM, raw_data, token_account
from .onchain import TOKEN_PROGRAM, TOKEN_2022_PROGRAM, uint
from .wallet import sdk

JUPITER_PROGRAM = 'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'
COMPUTE_PROGRAM = 'ComputeBudget111111111111111111111111111111'
LOOKUP_PROGRAM = 'AddressLookupTab1e1111111111111111111111111'
ROUTE = hashlib.sha256(b'global:route').digest()[:8]
SHARED_ROUTE = hashlib.sha256(b'global:shared_accounts_route').digest()[:8]


def decode(transaction):
    _, _, VersionedTransaction, _ = sdk()
    raw = base64.b64decode(transaction, validate=True)
    if not raw or len(raw) > 1232:
        raise ValueError('transaction exceeds Solana packet limit')
    tx = VersionedTransaction.from_bytes(raw)
    tx.sanitize()
    if tx.uses_durable_nonce():
        raise ValueError('durable nonce transactions are unsupported')
    return tx


def resolve_keys(message, rpc):
    _, Pubkey, _, _ = sdk()
    keys = [str(key) for key in message.account_keys]
    writable, readonly = [], []
    for lookup in getattr(message, 'address_table_lookups', []):
        result = rpc.call('getAccountInfo', [str(lookup.account_key), {'encoding':'base64','commitment':'confirmed'}])
        account = result['value']
        if not account or account['owner'] != LOOKUP_PROGRAM or account.get('executable') is not False:
            raise ValueError('invalid address lookup table')
        data = raw_data(account)
        if len(data) < 56 or (len(data)-56)%32 or int.from_bytes(data[:4],'little') != 1:
            raise ValueError('malformed address lookup table')
        entries = [str(Pubkey.from_bytes(data[index:index+32])) for index in range(56,len(data),32)]
        if len(entries) > 256:
            raise ValueError('oversized lookup table')
        try:
            writable.extend(entries[index] for index in lookup.writable_indexes)
            readonly.extend(entries[index] for index in lookup.readonly_indexes)
        except IndexError:
            raise ValueError('lookup table index is unavailable') from None
    return keys+writable+readonly


def inspect(transaction, wallet, input_ata, output_ata, input_mint, output_mint, quote, limits, rpc):
    tx = decode(transaction)
    message = tx.message
    if message.header.num_required_signatures != 1 or str(message.account_keys[0]) != wallet:
        raise ValueError('pilot requires wallet as sole signer and fee payer')
    if any(bytes(signature) != bytes(64) for signature in tx.signatures):
        raise ValueError('provider transaction unexpectedly contains a signature')
    keys = resolve_keys(message, rpc)
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate transaction account keys')
    routes, budget = [], {}
    for instruction in message.instructions:
        program = keys[instruction.program_id_index]
        accounts = [keys[index] for index in instruction.accounts]
        data = bytes(instruction.data)
        if program == COMPUTE_PROGRAM:
            if not data or data[0] not in (2,3) or data[0] in budget:
                raise ValueError('unsupported or duplicate compute budget instruction')
            if data[0] == 2 and len(data) == 5:
                budget[2] = int.from_bytes(data[1:], 'little')
                if not 0 < budget[2] <= 1400000:
                    raise ValueError('compute unit limit is invalid')
            elif data[0] == 3 and len(data) == 9:
                budget[3] = int.from_bytes(data[1:], 'little')
            else:
                raise ValueError('malformed compute budget instruction')
        elif program == ATA_PROGRAM:
            if data not in (b'', b'\x01') or len(accounts) < 6:
                raise ValueError('unsupported ATA instruction')
            if (accounts[0] != wallet or accounts[1] not in (input_ata,output_ata)
                    or accounts[2] != wallet or accounts[3] not in (input_mint,output_mint)
                    or accounts[4] != SYSTEM or accounts[5] not in (TOKEN_PROGRAM,TOKEN_2022_PROGRAM)):
                raise ValueError('ATA creation has an unexpected payer, owner or mint')
            from .live_rpc import ata
            if accounts[1] != ata(wallet, accounts[3], accounts[5]):
                raise ValueError('ATA derivation does not match instruction')
        elif program == JUPITER_PROGRAM:
            if len(data) < 31 or data[:8] not in (ROUTE,SHARED_ROUTE):
                raise ValueError('unsupported Jupiter route format; no signing override is provided')
            amount, expected_output, slippage, fee = struct.unpack('<QQHB', data[-19:])
            if (amount != uint(quote['inAmount']) or expected_output != uint(quote['outAmount'])
                    or slippage != limits.slippage_bps or fee != 0):
                raise ValueError('route amount, output, slippage or fee differs from quote')
            if data[:8] == ROUTE:
                if len(accounts) < 7 or accounts[1:4] != [wallet,input_ata,output_ata] or accounts[5] != output_mint:
                    raise ValueError('route authority, source or destination is wrong')
                if accounts[4] not in (JUPITER_PROGRAM,output_ata):
                    raise ValueError('route has an alternate destination')
            else:
                if (len(accounts) < 11 or accounts[2] != wallet or accounts[3] != input_ata
                        or accounts[6:9] != [output_ata,input_mint,output_mint]):
                    raise ValueError('shared route authority, source or destination is wrong')
            routes.append(dict(format='route' if data[:8] == ROUTE else 'shared_accounts_route',
                               input_amount=str(amount), expected_output=str(expected_output), slippage_bps=slippage))
        else:
            # Top-level transfers, approvals, authority changes and arbitrary programs cannot be signed.
            raise ValueError('unsupported top-level transaction program')
    if len(routes) != 1:
        raise ValueError('pilot requires exactly one Jupiter route')
    priority = 0
    if 3 in budget:
        if 2 not in budget:
            raise ValueError('priority fee requires explicit compute unit limit')
        priority = (budget[2]*budget[3]+999999)//1000000
        if priority > limits.max_priority_fee_lamports:
            raise ValueError('encoded priority fee exceeds cap')
    _, _, _, message_bytes = sdk()
    return tx, dict(message_hash=hashlib.sha256(message_bytes(message)).hexdigest(),
                    account_keys=keys, blockhash=str(message.recent_blockhash),
                    route=routes[0], priority_fee_lamports=priority)


def simulation_effects(before, after, input_ata, output_ata, quote, limits):
    wallet = before['wallet']
    accounts = before['accounts']
    post = after['accounts']
    if set(accounts) != set(post):
        raise ValueError('simulation account set differs from protected snapshot')
    native = post[wallet]
    if native is None or native['owner'] != SYSTEM or native.get('executable') is not False or raw_data(native):
        raise ValueError('simulation changed wallet ownership or account data')
    sol_cost = before['sol_lamports']-uint(native['lamports'])
    if sol_cost < 0 or sol_cost > limits.max_network_lamports or uint(native['lamports']) < limits.min_sol_reserve_lamports:
        raise ValueError('simulated SOL cost or remaining reserve violates limits')
    deltas = {}
    for key, original in accounts.items():
        if key == wallet:
            continue
        updated = post[key]
        old = token_account(original, wallet)
        new = token_account(updated, wallet)
        if old and (new is None or old['mint'] != new['mint'] or old['program'] != new['program']):
            raise ValueError('simulation closed or changed a protected token account')
        if original is not None and updated is not None:
            a, b = raw_data(original), raw_data(updated)
            if a[:64]+a[72:] != b[:64]+b[72:]:
                raise ValueError('simulation changed token authorities, delegate or extensions')
        elif new:
            data = raw_data(updated)
            if int.from_bytes(data[72:76],'little') != 0 or int.from_bytes(data[129:133],'little') != 0:
                raise ValueError('new token account has delegated or close authority')
        delta = (new['amount'] if new else 0)-(old['amount'] if old else 0)
        if key not in (input_ata,output_ata) and delta:
            raise ValueError('simulation changes an unrelated wallet balance')
        deltas[key] = delta
    source = token_account(post[input_ata], wallet, quote['inputMint'])
    destination = token_account(post[output_ata], wallet, quote['outputMint'])
    if not source or not destination:
        raise ValueError('simulation omitted source or destination token account')
    if deltas[input_ata] != -uint(quote['inAmount']) or deltas[output_ata] < uint(quote['otherAmountThreshold']):
        raise ValueError('simulation violates the approved input or minimum output')
    return dict(input_spent=str(-deltas[input_ata]), output_received=str(deltas[output_ata]), sol_cost_lamports=sol_cost)
