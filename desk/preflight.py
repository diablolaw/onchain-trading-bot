"""Deterministic screening evidence, explicitly separate from live execution readiness."""
import time

from .core import money, number, concentration
from .onchain import TOKEN_PROGRAM, TOKEN_2022_PROGRAM, uint, UnsupportedTokenFeature, MetadataIdentityMismatch
from .quotes import USDC


def fresh(stamp, now, max_age):
    stamp = number(stamp)
    return stamp is not None and 0 <= now-stamp <= max_age


def quote_reasons(token, roundtrip, rules, now):
    reasons = []
    q = roundtrip or {}
    if q.get('token') != token or q.get('quote_currency') != USDC or number(q.get('ticket_usdc')) != rules.ticket:
        reasons.append('quote_identity_or_ticket_mismatch')
    buy, sell = q.get('buy') or {}, q.get('sell') or {}
    if (buy.get('input_mint') != USDC or buy.get('output_mint') != token
            or sell.get('input_mint') != token or sell.get('output_mint') != USDC
            or sell.get('input_amount') != buy.get('minimum_output')):
        reasons.append('invalid_roundtrip_binding')
    try:
        expected = money(rules.ticket)*1000000
        if uint(buy['input_amount']) != expected or uint(sell['input_amount']) != uint(buy['minimum_output']):
            raise ValueError('invalid quote amounts')
        loss = max(0, float((1-money(uint(sell['minimum_output']))/money(uint(buy['input_amount'])))*100))
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        loss = None
        reasons.append('invalid_quote_amounts')
    for side, quote in (('buy', buy), ('sell', sell)):
        if not fresh(quote.get('observed_at'), now, rules.max_preflight_age):
            reasons.append(side+'_quote_stale')
        expiry = number(quote.get('expire_at'))
        if quote.get('expire_at') is not None and expiry is None:
            reasons.append(side+'_invalid_expiry')
        elif expiry is not None and expiry <= now:
            reasons.append(side+'_quote_expired')
        try:
            minimum, output = uint(quote['minimum_output']), uint(quote['output_amount'])
            if not 0 < minimum <= output or minimum < output*(10000-uint(quote['slippage_bps']))//10000:
                raise ValueError('invalid output bounds')
        except (ValueError, TypeError, KeyError):
            reasons.append(side+'_invalid_output_bounds')
        for key, limit, label in (('price_impact_pct', rules.max_price_impact_pct, 'price_impact'),
                                  ('slippage_bps', rules.max_quote_slippage_bps, 'slippage'),
                                  ('fee_bps', rules.max_quote_fee_bps, 'fee')):
            value = number(quote.get(key))
            if value is None or value > limit:
                reasons.append(side+'_'+label+'_limit')
        if quote.get('quote_only') is not True or quote.get('router') not in ('metis','jupiterz','dflow','okx'):
            reasons.append(side+'_invalid_route_evidence')
    if loss is None or loss > rules.max_roundtrip_loss_pct:
        reasons.append('roundtrip_loss_limit')
    gas = number(q.get('network_lamports'))
    if gas is None or gas > rules.max_quote_network_lamports:
        reasons.append('quote_network_cost_limit')
    legs_gas = [number(leg.get('network_lamports')) for leg in (buy, sell)]
    if any(value is None for value in legs_gas) or gas != sum(value or 0 for value in legs_gas):
        reasons.append('inconsistent_quote_network_cost')
    return reasons


def preflight_reasons(token, evidence, rules, now, *, check_quotes=True):
    evidence = evidence or {}
    reasons = [e for e in (evidence.get('errors') or []) if check_quotes or e != 'quote_evidence_unavailable']
    mint = evidence.get('mint') or {}
    if mint.get('token') != token or not fresh(mint.get('observed_at'), now, rules.max_holder_age):
        reasons.append('missing_or_stale_mint_evidence')
    if mint:
        if mint.get('program') not in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM) or mint.get('supported_features') is not True:
            reasons.append('unsupported_token_features')
    elif 'unsupported_token_features' not in reasons:
        reasons.append('token_features_evidence_missing')
    for key in ('mint_authority', 'freeze_authority'):
        if key not in mint:
            reasons.append(key+'_evidence_missing')
        elif mint[key] is not None:
            reasons.append(key+'_not_revoked')
    owners = evidence.get('holders') or {}
    if (owners.get('token') != token or owners.get('method') != rules.concentration_measure
            or not fresh(owners.get('observed_at'), now, rules.max_holder_age)
            or not owners.get('source') or (
                owners.get('accounted_supply') != mint.get('supply') if rules.concentration_measure=='owner_aggregated' else
                owners.get('supply') != mint.get('supply') or owners.get('supply_verified') is not True)):
        reasons.append('missing_or_unreconciled_owner_evidence')
    top,top10=concentration(owners,rules)
    if top is None or top10 is None or not top <= top10 <= 100:
        reasons.append('invalid_owner_concentration')
    elif top > rules.max_top_owner_pct or top10 > rules.max_top10_owner_pct:
        reasons.append('owner_concentration')
    if check_quotes:reasons.extend(quote_reasons(token, evidence.get('quotes'), rules, now))
    return list(dict.fromkeys(reasons))


def collect(token, rules, rpc, jupiter, *, check_quotes=True):
    evidence = dict(token=token, mint=None, holders=None, quotes=None, errors=[])
    try:
        evidence['mint'], evidence['holders'] = rpc.inspect(token)
    except MetadataIdentityMismatch as error:
        evidence['errors'].append('metadata_identity_mismatch')
        evidence['onchain_error'] = str(error)
        evidence['onchain_error_kind'] = 'metadata_identity_mismatch'
    except UnsupportedTokenFeature as error:
        evidence['errors'].append('unsupported_token_features')
        evidence['onchain_error'] = str(error)
        evidence['onchain_error_kind'] = 'unsupported_token_feature'
    except (ValueError, RuntimeError, KeyError, TypeError) as error:
        evidence['errors'].append('onchain_evidence_unavailable')
        evidence['onchain_error_kind'] = 'unavailable_or_invalid_evidence'
        saved=getattr(rpc,'last_mint',None)
        if isinstance(saved,dict) and saved.get('token')==token: evidence['mint']=saved
        evidence['onchain_error'] = str(error)
    try:
        if check_quotes:evidence['quotes'] = jupiter.roundtrip(token, rules.ticket, rules.max_quote_slippage_bps)
    except (ValueError, RuntimeError, KeyError, TypeError) as error:
        evidence['errors'].append('quote_evidence_unavailable')
        evidence['quote_error'] = str(error)
    now = time.time()
    evidence['observed_at'] = now
    evidence['reasons'] = preflight_reasons(token, evidence, rules, now, check_quotes=check_quotes)
    evidence['screen_passed'] = not evidence['reasons']
    evidence['ready_for_live'] = False
    return evidence
