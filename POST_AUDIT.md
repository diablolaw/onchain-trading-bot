# Historical audit — see current status in POST_SETUP.md

This document records the 9 October baseline. Its unconnected FOMO statements are historical; the Chrome market bridge was subsequently verified.

# Audit against the original Grok Bot post

Reviewed 9 October 2026 against the full original article supplied by the user, not the later Jev article. This is a code/configuration audit, not validation of the author's performance claims. FOMO bridge development was interrupted by this audit request; its integration is not installed or verified against a live browser session.

## Overall finding

The current application is a local Solana paper scanner with deterministic admission, a Grok API analysis step, Jev selection, SQLite logging and a separate paper risk worker. It does **not** reproduce the original desk. Earlier changes mixed the original article with its later Jev follow-up. Numerical alignment to the later article should not have been described as matching this original post.

## Stage-by-stage comparison

| Area | Original article | Current implementation | Assessment |
|---|---|---|---|
| Runtime | CHIEF plus six separate Grok Bot cloud seats | Local Python process, Grok text API, Jev API, separate paper risk process | Different architecture. No cloud seats or CHIEF supervisor |
| Universe | DexScreener launch/search data, 30-second polling | GeckoTerminal two-page launch queue; optional DexScreener `pump`/`raydium` search sample; five-minute AI cadence | Different source/cadence. Query choice is ours; article does not define its entire universe |
| Market fields | Price, FDV, cap, m5/h1/h6/h24 volume and price changes, per-window trades, dex ID | Normalized price, cap, liquidity, creation time, h6/h24 volume and h1/h24 trades | Missing normalized FDV, dex ID, short-window volumes, price-change windows and additional trade windows |
| Ranking | Holder growth first; warning when price outruns holders; sell dominance in two windows; at most ten | Optional comparison ranking uses successive observations, holder-growth fallback and sell dominance | Partial. Holder counts are collected only for limited dossiers; hourly observations overlap. Diagnostic report can contain up to 200 observations rather than a distinct ten-item SCAN shortlist |
| Context | Local WorldMonitor; context can move an existing candidate one rank, never create one | `world_context=None`; no server/client | Missing |
| Concentration | Largest token account / total supply, reject above 5%; unavailable RPC rejects | Pilot token-account mode now uses the specified RPC methods and threshold | Closest match. Potential vaults remain included exactly as this calculation does. Top-ten-account check is an additional rule |
| VET shape | Flat holders with rising price; already-priced information; declining liquidity on young pools | Flat-holder rule in comparison mode; history warmup; generic AI shape judgement | Partial. No faithful implementation of already-priced/event evidence or young-pool liquidity-fall rule |
| Story verification | Cross-source verification through named WorldMonitor endpoints; absent story skips | No world claims collected or verified | Missing |
| Missing context | Explicit degraded scan and PASS_PARTIAL with reduced sizing | Provider errors can degrade cycle; fixed ticket; no general PASS_PARTIAL admission/sizing | Different |
| Hard thresholds | Specifies 5% concentration; minimum age/liquidity and fee ceilings remain configuration-dependent | Adds 15m–72h, $12k liquidity, $40k volume, $60k–$8m cap, 150 trades, 80 holders, top-ten 60% | These numbers came from the later Jev guide. They are not specified by this original article |
| AI admission | Grok Bot seats with supplied prompts | Grok analysis veto followed by Jev custom `supported/fading/insufficient` questions; 0.8 probability, 0.7 confidence | Different. Original article does not specify Jev or these thresholds |
| BOOK | Prediction-market discovery, actual Polymarket/Kalshi prices, independent forecasts, edge above 8%, at most seven books | Solana only; no BOOK | Entire branch missing. The article attributes its stated final daily gains to prediction markets, which ours cannot trade |
| SIZE | Kelly sizing capped at 6%; free/locked capital, liquidity adjustment, fees, cost reserve; one permitted retest add | Fixed $5 pilot ticket; one position; $5.25 exposure cap; cash and liquidity checks | Different. No Kelly inputs, locked bag, operating-cost reserve or retest add |
| FILLS | FOMO-only execution, dedicated venue wallet, venue fee floor, partial-fill/slippage reporting, whale distribution hold | Paper fills from price plus configured costs; separate manual Jupiter live prototype | Different venue and execution semantics. No FOMO trading connection or whale-distribution detector |
| RISK volume trigger | `volume.h6 / (volume.h24 / 4) < 0.20` | Same volume trigger exists | Arithmetic matches |
| Other exits | Hold through +50%; mark +60% and continue; stated single volume exit | Stop loss -15%, take profit +30%, liquidity deterioration, maximum hold 24h, then volume decay | Material strategy mismatch: take-profit rule prevents the specified runner behaviour |
| Missing exit data | Retry twice, then close anyway | Logs blocked fetch or stale price and leaves paper position open | Different. A real executable close after missing data also needs quote/execution handling absent from this article |
| Timing | RISK every five minutes; send within 60 seconds after close condition | Paper risk defaults to 15 seconds; no verified end-to-end live latency guarantee | Different cadence, no live proof |
| Supervision | 60-second heartbeats, bounded restarts, halt on repeated failure, WorldMonitor readiness checks | User-managed terminals; no supervisor | Missing |
| Reporting | Telegram end-of-day report; material-change notifications; bank/working-capital P&L | SQLite and CLI reports | Local audit exists; Telegram and specified reporting automation missing |

## FOMO fee mismatch

The original article states approximately 0.45% with a $0.95 minimum per trade. These are the article's numbers, not verified current venue fees. Our paper defaults use 30 bps plus $0.05, and Jupiter quotes are not FOMO venue quotes. Applying the article's floor to a $5 order means 19% for one trade; entry plus exit floors alone would be approximately 38% of initial ticket, before other costs. Consequently the current $5 engineering pilot cannot be presented as a faithful FOMO economics experiment. The article leaves the maximum acceptable fee percentage undefined.

## FOMO adapter status

`desk/fomo.py` and `integrations/fomo-chrome/` are new, uninstalled adapter files. Local bridge/normalization tests pass, but real Chrome header capture, token refresh, live response shape and FOMO server acceptance are unverified. The extension's authorization currently lives in service-worker memory and will be lost on worker restart; unattended refresh behaviour is unfinished. The article imports a session helper without publishing its implementation. Our adapter is an alternative implementation, not copied working connector code. Its Chrome permissions require review before installation. Logging into FOMO alone did not connect Python.

FOMO enrichment uses token-level free-cut metrics without overwriting pair-level volume used for risk arithmetic. This avoids mixing token h24 volume with one pair's h6 volume. A matching venue fee/fill model is still absent. Current collection fetches DexScreener batches before applying the venue free cut, unlike the later guide's network funnel. `ai-status` still has a static FOMO integration label, not verified connection evidence.

## What can and cannot be reproduced from the text

The prompts specify roles and several rules, but omit full search queries, holder indexer selection, numerical minimum age/liquidity, fee ceiling, Kelly probability/payoff inputs, maximum spread, locked-capital allocation, whale-distribution measurement and executable FOMO integration. No code can uniquely reproduce those choices from the article alone. The article also states five-minute risk polling and sub-minute reaction after a condition becomes true; polling alone cannot guarantee sub-minute detection. RISK's immediate close language and whale-hold language lack explicit conflict precedence.

## Recommended implementation sequence

1. Separate an explicitly named original-post paper profile from the later-Jev profile and the existing engineering pilot; preserve historical evidence and account balances.
2. Complete and verify FOMO market enrichment and use an accurate paper venue fee model. Do not fund a venue merely to validate collection.
3. Add the missing DexScreener windows, reliable indexed holder history, configurable original discovery and distinct ten-item shortlist.
4. Add local WorldMonitor health/context/story checks and explicit partial verdicts.
5. Implement explicit sizing inputs and original runner/volume-exit behaviour in paper mode; define missing-data and whale-hold conflict handling.
6. Add supervisor and reporting. Treat BOOK as a separate substantial milestone, not a minor scanner addition.
7. Connect Grok Bot cloud seats and venue execution only after the paper workflow and external integrations are proven. No real-money order is authorized by this audit.

There is no evidence that removing the differences will guarantee qualifying trades or profitability. The previous zero-approval run is evidence about our implemented filters and sampled universe, not a replication result for the author's desk.
