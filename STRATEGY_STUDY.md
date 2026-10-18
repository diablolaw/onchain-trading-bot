# Strategy study: what happened after the desk looked at a token

Data: both paper databases, 10 to 16 Oct 2026. Analysis only; nothing on the running desk was changed.

## Method

- **Population.** Every token that passed the free cut, one entry point per token per 3 hours: 676 entry points. Of those, 625 had enough later prices to simulate a trade, 374 had a price one hour later, and 271 had one three hours later.
- **Prices.** Taken from the DexScreener responses the desk recorded every 30 seconds for the same pool.
- **Simulated trade.** Buy at the recorded price plus 2%, sell at the market price less 2%, minus the $1.90 in fees: 4.2% of a $45 ticket, 2.5% of a $75 one.
- **Rug.** Counted as a full loss when pool liquidity fell below half its entry level.
- **Comparisons.** Grouped by what the desk knew at the time.

## Headline

**No combination of tokens and exit rules made money on average.** Across all 625 simulated entries, the current exits lose 15.7% per trade on average at $45, with 29% winners. Every group and every exit rule tested came out negative on average.

- **The typical move doesn't cover costs.** One hour after the desk looked, the median token was down 0.6%, and only 28% had risen more than the 8% a $45 round trip costs.
- **Rugs carry much of the damage.** 72 of the 625 entries hit the rug test, and 64 of those were tokens under an hour old.

## What came before better and worse results

Average per trade, current exits, $45 ticket:

| Group | Average | Note |
|---|---|---|
| All tokens | -15.7% | 625 entries |
| Under 1 hour old | -17.6% | 19% rug rate |
| 6 to 48 hours old | -10.8% | almost no rugs |
| Just dipped (last hour below -10%) | -8.0% | best single signal |
| Just spiked (last 5 minutes above +10%) | -21.5% | worst |
| Heavy buying (buys at least 1.6x sells, last hour) | -21.5% | buying into a crowd |
| Jev said momentum spent (0.6 or more) | -3.0% | 37 entries |
| Jev said momentum not spent (under 0.6) | -16.7% | 46 entries; these are the ones the desk buys |
| Grok evidence within the hour | -12.1% | 55 entries; slightly better than average |
| Pool at least 90% locked | -11.5% | fewer rugs than unknown pools |

**The most important finding is about Jev's momentum question.** Since the wording change on 12 Oct, the tokens Jev called "not spent" did much worse than the ones it called "spent". The desk only buys the "not spent" ones, so on this data the gate is choosing the worse half.

Shape answers didn't separate winners from losers in any useful way: crowd -7%, fading -6%, one buyer -15%, too early -20%, all on small numbers.

## Exit rules

| Exit rule | All tokens, $45 | Older dips, $75 |
|---|---|---|
| Current (20% trail, 10% after +20%, take profit +50%) | -15.7% | -4.0% |
| Take profit at +15%, 20% stop | -13.8% | -1.4% (53% winners) |
| 10% stop, take profit +30% | -13.9% | -3.3% |
| Hold 1 hour, then sell | -19.2% | -1.5% |
| Hold 3 hours, then sell | -22.2% | -5.0% |

The "older dips" column is tokens 6 to 48 hours old whose price fell more than 10% in the previous hour: 64 entries. It was the least bad combination found. The $75 ticket cuts the fee share from 4.2% to 2.5%.

## Caveats

- **Hindsight.** The groups above were chosen after looking at the results. With this many comparisons, some differences will be luck. The -1.4% for older dips with a +15% take-profit is a direction to test forward, not a result.
- **Survivorship.** Tokens that vanish from the feed stop having prices. A dead token is valued at its last price seen, which flatters the losers.
- **Gaps.** Prices are 30 seconds to 10 minutes apart, so stops and take-profits fill at the next recorded price, not at the exact trigger.
- **Costs.** They are the desk's own assumptions: 2% slippage each way and a $0.95 fee each way, both confirmed by you. Real slippage on thin pools was often higher.
- **One market, five days.**

## What this suggests

1. **Don't treat this strategy as ready for real money.** On five days of data, no tested version of it had a positive average.
2. **If you keep testing on paper, change what it hunts for.** Target tokens 6 to 48 hours old after a pullback, use a quick take-profit around +15%, and use the full $75 ticket. Run it forward for a week to see whether the hindsight result holds.
3. **Remove or reverse the momentum gate.** On this data it selects the worse half.
4. **Keep the lock check and the rug protections.** Young-token rugs are the biggest single source of loss.
