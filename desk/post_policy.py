"""Post guide admission and sizing; missing edge inputs never become Kelly bets."""
import math

def soft_reasons(market,chain,social=None):
    reasons=[]
    if market['shape']['choice'] in ('fading','one_buyer'):reasons.append('shape')
    # "Too early" is missing history, not a bad shape: it gets its own reason so the bench stays short
    # and the token is judged again once there is more to see.
    if market['shape']['probabilities']['crowd']<.55:reasons.append('shape_too_early' if market['shape']['choice']=='too_early' else 'shape_weak')
    for answers,key,threshold,maximum in (
        (market,'liquidity_fits_ticket',.60,False),(market,'momentum_already_spent',.60,True),
        (chain,'concentration_is_exit_risk',.55,True),(chain,'dev_still_loaded',.55,True),
        (chain,'sellable_by_evidence',.60,False)):
        if key in answers and (answers[key]['noul']>threshold if maximum else answers[key]['noul']<threshold):reasons.append(key)
    if 'authority_risk' in chain and chain['authority_risk']['choice']!='renounced':reasons.append('authority_open')
    if 'sell_side_risk' in chain and chain['sell_side_risk']['choice']!='clean':reasons.append('sell_side')
    if social:
        for key,threshold,maximum in (('account_is_the_project',.70,False),('audience_is_real',.45,False),('recycled_account',.50,True)):
            if social[key]['noul']>threshold if maximum else social[key]['noul']<threshold:reasons.append(key)
        if social['effort']['score']<1:reasons.append('effort')
    return reasons


def pick_passed(answers):
    return answers['worth_trading_at_all']['noul']>=.60 and answers['best']['confidence']>=.55


def size(free_cash,bank,probability,payoff,liquidity,partial=False,dark=False,locked_cash=0,operating_reserve=0,max_fee_pct=3,liquidity_fraction=.02):
    values=(free_cash,bank,probability,payoff,liquidity,locked_cash,operating_reserve,max_fee_pct,liquidity_fraction)
    if any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in values) or not 0<=probability<=1 or payoff<=0:raise ValueError('invalid explicit Kelly inputs')
    fraction=max(0,min(.06,(probability*(payoff+1)-1)/payoff))
    return finish(free_cash,bank,fraction,liquidity,partial,dark,locked_cash,operating_reserve,max_fee_pct,liquidity_fraction,'explicit_kelly_inputs')


def fixed_size(free_cash,bank,fraction,liquidity,partial=False,dark=False,locked_cash=0,operating_reserve=0,max_fee_pct=3,liquidity_fraction=.02,max_fraction=.06):
    """Paper-only fixed fraction of the bank while no calibrated p/payoff exists.

    Not Kelly: Jev confidence is never used as a win probability. Same 6% cap, liquidity cap,
    partial cut and fee check as the Kelly path, so shadow results stay comparable."""
    values=(free_cash,bank,fraction,liquidity,locked_cash,operating_reserve,max_fee_pct,liquidity_fraction)
    if any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in values) or not 0<fraction<=max_fraction<=1:raise ValueError('invalid paper fixed fraction inputs')
    return finish(free_cash,bank,fraction,liquidity,partial,dark,locked_cash,operating_reserve,max_fee_pct,liquidity_fraction,'paper_fixed_fraction_assumption')


def finish(free_cash,bank,fraction,liquidity,partial,dark,locked_cash,operating_reserve,max_fee_pct,liquidity_fraction,reason):
    available=max(0,free_cash-locked_cash-operating_reserve)
    dollars=min(available,bank*fraction,liquidity*liquidity_fraction)
    if partial:dollars*=.60
    if dark:dollars*=.40
    fee=max(dollars*.0045,.95)
    break_even_pct=(2*fee/dollars*100) if dollars else None
    if not dollars or break_even_pct>max_fee_pct:
        return dict(dollars=0,reason='no_edge_or_fee_viability',break_even_pct=break_even_pct)
    return dict(dollars=round(dollars,8),fraction=fraction,break_even_pct=break_even_pct,fee_assumption='article_45bps_floor_0.95_unverified_current',reason=reason)
