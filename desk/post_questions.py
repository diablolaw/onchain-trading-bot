"""Named Jev sets for the follow-up desk; arithmetic and admission stay in code."""
def noul(text):return dict(type='noul',instructions=text)
def choice(text,options):return dict(type='choice',instructions=text,criteria=options)
def score(text,levels):return dict(type='score',instructions=text,criteria=levels)

CONCENTRATION=noul('Do supplied holder distributions indicate dangerous exit concentration?')
DEV=noul('Would the disclosed developer stake threaten exit liquidity?')
SETS={
 'market':dict(shape=choice('Classify participation using supplied temporal evidence.',dict(crowd='Holder growth exceeds price growth; buying leads in both recent windows and volume persists across observations.',one_buyer='Price rises while holder growth is flat or slower, suggesting concentrated buying.',fading='Recent volume weakens against the daily baseline or selling dominates both windows.',too_early='Evidence is too sparse to distinguish these patterns.')),
 liquidity_fits_ticket=noul('Is the intended ticket exitable within the supplied liquidity budget?'),momentum_already_spent=noul('Is the relevant move already reflected in price? Compare older and recent change windows; do not mistake an already completed move for a fresh opportunity. When pool_age_hours is under 24 the 24h change is the whole move since launch, so judge whether the move is still under way from the 1h and 6h windows and the observations. A falling 1h change means the move is spent, whatever the pool age.')),
 'chain_solana':dict(authority_risk=choice('Classify explicitly supplied mint and freeze authorities.',dict(renounced='Both revoked.',mint_open='Mint active.',freeze_open='Freeze active.',both_open='Both active.')),concentration_is_exit_risk=CONCENTRATION,dev_still_loaded=DEV),
 'chain_bsc':dict(sell_side_risk=choice('Classify supplied sellability evidence.',dict(clean='Sells confirmed, no flag.',flagged='Honeypot flag.',suspicious='Abnormal sell scarcity.',unknown='Missing evidence.')),concentration_is_exit_risk=CONCENTRATION,pool_quality=score('Rate pool exit capacity.', ['Fragile','Usable','Deep'])),
 'chain_robinhood':dict(data_coverage=choice('Classify supplied data coverage.',dict(indexed='Complete holder and contract data.',partial='Some holder or contract data.',dark='Only market data.')),sellable_by_evidence=noul('Do recent trade windows show normal selling?'),concentration_is_exit_risk=CONCENTRATION,dev_still_loaded=DEV),
 'social':dict(account_is_the_project=noul('Does x_account match published_handle, discuss this token narrative, and link back to this project rather than a fan or impersonator?' ),audience_is_real=noul('Are replies and reposts on recent posts plausible for the follower count?' ),recycled_account=noul('Do account age, prior handles or older content show a different project repurposed for this token?' ),effort=score('Rate demonstrated project effort.', ['Only an isolated image or post','Several promotional posts','Regular substantive work beyond price','Visible team delivering identifiable work']))}

def questions(name,state):
    if name!='pick':
        if name not in SETS:raise ValueError('unknown question set')
        return SETS[name]
    candidates=state.get('candidates')
    if not isinstance(candidates,list) or not 1<=len(candidates)<=10:raise ValueError('pick requires 1..10 candidates')
    options={}
    for c in candidates:
        if not isinstance(c,dict) or not isinstance(c.get('token'),str) or c['token'] in options or not isinstance(c.get('summary'),str):raise ValueError('invalid or duplicate pick candidate')
        options[c['token']]=c['summary']
    return dict(best=choice('Select the strongest supplied candidate by exact token identifier.',options),worth_trading_at_all=noul('Does any supplied candidate justify an entry on absolute evidence?'))
