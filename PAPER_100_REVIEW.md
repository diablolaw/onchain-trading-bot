# $100 paper run review

Source: `data/post-paper-100.sqlite`, read-only, as of 18:20 UTC on 11 Oct 2026. All times are UTC (BST is one hour ahead). The desk was still running while I read it.

## Result

- 62 decision cycles from 15:09 on 10 Oct to 18:14 on 11 Oct. Cash is still $100.00, with no positions and no fills.
- Jev was called 72 times on 12 tokens and approved none.
- The Mac was suspended, not crashed: the last cycle before the gap was 02:31 (03:31 BST), and the same workers carried on at 13:47. This is inferred from the cycle timing. A second gap of about 45 minutes followed the 16:23 restart.

## Rejection funnel

| Stage | In | Out | Why it stopped |
|---|---|---|---|
| Feed | 170 to 246 age-eligible tokens per cycle | top 100 by 24h volume go on | |
| Free cut (FOMO numbers) | 4,901 candidate looks, 2,024 unique tokens | 260 passed (5.3%), 48 unique tokens | market cap 3,441, liquidity 3,425, volume 2,899, FOMO had no data 1,222, age 16 |
| Dossier and hard rules | 260 | 73 | 130 deferred because only 3 dossiers fit in a cycle; 20 sells outnumber buys in 1h and 6h; 9 stale price; 8 holders flat while price rises; 7 on-chain data missing; 6 FOMO data missing at refresh; 5 top wallet concentration; 2 too few trades; 1 too few holders |
| Wait for Grok social | 73 | 35 | 38 holds waiting for an answer that mostly never came |
| Jev | 35 judged (12 tokens) | 0 | 34 soft rejects, 1 judge error |
| Pick, size, fill | 0 | 0 | |

Notes on the funnel:

- The free cut is not the problem. 82% of the tokens it rejected failed liquidity, market cap and volume all at once, and only 32 missed on a single measure. Almost every market cap failure was below $60k (6 were above $8M).
- The 9 stale price rejections all happened before 18:44 on 10 Oct. None since the re-fetch change.
- 36 tokens were deferred at least once and 14 of them never got a dossier at all.

## Every Jev call

Jev model was `jev-1.13.0` throughout. Limits: momentum must be 0.60 or lower, crowd 0.55 or higher, shape not fading or one buyer, concentration and developer risk 0.55 or lower.

| Time | Token | Shape (crowd prob.) | Momentum spent | Conc. / dev | Verdict |
|---|---|---|---|---|---|
| 10 Oct 18:47 | qOMPUTE | one buyer (0.32) | 0.70 | 0.36 / 0.38 | reject: shape, momentum. Social passed (0.86 project, 0.60 audience, effort 1.49) |
| 19:48 | qOMPUTE | crowd (0.74) | 0.70 | 0.42 / 0.41 | reject: momentum only |
| 19:48 | SWOLF | crowd (0.85) | 0.64 | 0.30 / 0.14 | reject: momentum only |
| 20:18 | QUBIT | one buyer (0.01) | 0.66 | 0.29 / 0.23 | reject: shape, momentum |
| 20:18 | QI | fading (0.01) | 0.77 | 0.29 / 0.10 | reject: shape, momentum |
| 21:33 | Altai (Gyk9) | fading (0.48) | 0.70 | 0.21 / 0.07 | reject: shape, momentum |
| 22:03 | Jarvis | one buyer (0.02) | 0.69 | 0.29 / 0.15 | reject: shape, momentum |
| 22:03 | SECRETFROG | fading (0.12) | 0.67 | 0.28 / 0.15 | reject: shape, momentum |
| 22:03 | Altai (FDJq) | fading (0.10) | 0.74 | 0.31 / 0.22 | reject: shape, momentum |
| 22:18 | SWOLF | one buyer (0.48) | 0.69 | 0.25 / 0.12 | reject: shape, momentum |
| 22:18 | QI | one buyer (0.01) | 0.65 | 0.28 / 0.11 | reject: shape, momentum |
| 23:16 | QCAT | fading (0.19) | 0.73 | 0.30 / 0.15 | reject: shape, momentum |
| 23:16 | QI | one buyer (0.01) | 0.71 | 0.28 / 0.11 | reject: shape, momentum |
| 23:31 | SECRETFROG | fading (0.02) | 0.72 | 0.30 / 0.16 | reject: shape, momentum |
| 23:46 | QCAT | one buyer (0.01) | 0.72 | 0.31 / 0.15 | reject: shape, momentum |
| 23:46 | SWOLF | one buyer (0.01) | 0.73 | 0.29 / 0.14 | reject: shape, momentum |
| 23:46 | QI | fading (0.12) | 0.79 | 0.31 / 0.11 | reject: shape, momentum |
| 11 Oct 00:01 | SECRETFROG | crowd (0.56) | 0.67 | 0.27 / 0.15 | reject: momentum only |
| 00:16 | SWOLF | fading (0.02) | 0.66 | 0.29 / 0.15 | reject: shape, momentum |
| 00:16 | QCAT | fading (0.09) | 0.73 | 0.30 / 0.15 | reject: shape, momentum |
| 00:16 | QI | one buyer (0.01) | 0.71 | 0.30 / 0.11 | reject: shape, momentum |
| 00:31 | SECRETFROG | crowd (0.80) | 0.66 | 0.28 / 0.14 | reject: momentum only |
| 01:01 | SECRETFROG | one buyer (0.24) | 0.68 | 0.29 / 0.16 | reject: shape, momentum |
| 01:16 | SWOLF | one buyer (0.01) | 0.66 | 0.30 / 0.15 | reject: shape, momentum |
| 01:31 | SECRETFROG | fading (0.16) | 0.64 | 0.30 / 0.15 | reject: shape, momentum |
| 01:31 | QUBIT | fading (0.00) | 0.77 | 0.29 / 0.22 | reject: shape, momentum |
| 02:01 | LAUNCHER | crowd (0.93) | 0.72 | 0.33 / 0.35 | reject: momentum only |
| 02:16 | SECRETFROG | crowd (0.94) | 0.61 | 0.26 / 0.16 | reject: momentum only |
| 02:31 | SWOLF | fading (0.39) | 0.66 | 0.29 / 0.16 | reject: shape, momentum |
| 14:24 | QT | crowd (0.69) | 0.69 | 0.56 / 0.56 | reject: momentum, concentration, developer stake |
| 14:24 | SECRETFROG | crowd (0.43) | 0.68 | 0.26 / 0.15 | reject: weak crowd, momentum |
| 14:38 | SIB | fading (0.04) | 0.71 | 0.28 / 0.28 | reject: shape, momentum |
| 14:39 | Jarvis | one buyer (0.01) | 0.71 | 0.31 / 0.15 | reject: shape, momentum |
| 17:17 | SECRETFROG | one buyer (0.13) | 0.65 | 0.30 / 0.15 | reject: shape, momentum |
| 17:47 | QT | crowd (0.49) | 0.67 | 0.52 / 0.54 | judge error on the social answer (bug, now fixed); momentum would have rejected it anyway |

Every call also passed "liquidity fits the ticket" (0.80 to 0.88) and had both authorities renounced.

### Does momentum at 0.60 dominate?

Yes. It failed all 35 market calls (range 0.61 to 0.79, median 0.69) and was the only reason on 6 of them. Shape failed 27.

But Jev was reading the evidence correctly. 28 of the 35 had already risen 90% to 1,979% in 24 hours, and the 1h change was negative on 26. The desk sends the top 100 tokens by 24h volume to the free cut, so nearly everything that reaches Jev has already had its run.

What the prices did afterwards (price now is from DexScreener at 18:24 on 11 Oct, same pool):

| Token | Now vs first Jev call |
|---|---|
| qOMPUTE | down 99% |
| LAUNCHER | down 98% |
| Altai (FDJq) | down 90% |
| QUBIT | down 74% |
| Altai (Gyk9) | down 67% |
| QI | down 14% |
| QT | down 10% |
| SIB | down 7% |
| SWOLF | up 8% |
| Jarvis | up 87% |
| QCAT | up 90% |
| SECRETFROG | up 105% |

If the limit had been 0.70, five calls would have passed everything Jev checks: qOMPUTE at 19:48 (it fell 76% within three hours), SWOLF at 19:48 (between +18% and -24% over six hours), and SECRETFROG three times (down 26% within the hour before recovering). None of the five would have traded, for the reason in the next section.

## The rule that actually blocks a $100 bank

A candidate judged without Grok evidence gets a 0.6 size cut: 0.6 x $40 = $24. The fee floor of $0.95 each way is 7.9% of $24, above the 5% round-trip ceiling, so sizing returns zero. Only 2 of the 35 judged candidates had Grok evidence, so 33 of 35 could not have traded whatever Jev said.

The full $40 ticket only just clears (4.75%). Below $95 of cash it fails too, so at these settings a bank under $100 cannot open any position, and the paper account would lock after losing about $5.

The $0.95 floor is marked `unverified_current` in `desk/post_policy.py`. It is worth confirming before changing anything else, because the whole constraint moves with it.

## Outages

**FOMO.** 13 of 62 cycles had no FOMO data and 1 was partial, which cost 1,222 candidate looks (26%). They came in blocks: 16:24, 17:42 to 18:03, 19:14 to 19:29, 20:44 to 21:14 and 22:29 to 22:44 on 10 Oct, then 14:49 to 15:19 on 11 Oct. The errors were `fomo_headers_too_large_reload_fomo`, `fomo_headers_too_large_cookie_token_missing` and `session_not_observed_reload_fomo`, which is the login token lapsing and the extension falling back to cookies.

No `fomo:error` row contains bridge diagnostics or `page_token_seen`. The code that records them only started running at the 16:23 restart on 11 Oct, and there has been no batch error since. The live bridge at 18:20 reported `page_token_seen: true`, `session_renewals: 1`, about 38 minutes left on the token, and `cookie_token_found: false`. One refresh of three tokens still failed at 18:01 with `session_not_observed_reload_fomo`. So the page-token fix looks better but has run for two hours; I would not call it verified until it has ridden through several token expiries.

**Grok social.** Evidence arrived for 9 tokens in total: 4 between 16:47 and 18:38 on 10 Oct, then nothing for 22 hours 45 minutes, then 5 between 17:24 and 18:01 on 11 Oct after the tunnel was restarted at 17:18. 15 jobs sit unanswered in `social_jobs`. The database cannot say whether the tunnel or the Grok seat was down overnight, and `data/tunnel.log` only covers the latest start. A quick tunnel gets a new hostname on every start, so the seat has to be given the new address each time. That is the fragile part.

**QuickNode.** The first 11 dossiers (15:26 to 16:10 on 10 Oct) all failed with `provider HTTP 413` on `getMultipleAccounts`, which booked 7 rejections. The fallback to one `getAccountInfo` per account went in at about 16:11 and the 110 dossiers since have had no on-chain error. The cost is speed: a dossier now takes about 80 seconds, which is why only 3 fit in a cycle.

## Bugs fixed locally

Nothing is committed or pushed. `python3 -m unittest discover -s tests` passes (159 tests, 26 skipped). The running desk still has the old code until it is restarted.

1. `desk/ai.py`: a valid Jev score answer was thrown away when its two-decimal probabilities summed to 0.99. This discarded 1 of the 2 social answers in the run. The check now allows rounding of half a point per level and nothing more.
2. `desk/post_evaluate.py`: an unanswered handle restarted its 30 minute wait every hour, because the job clock is reset when the job is requeued. Jarvis and SWOLF each waited six or more separate times. The wait now counts from the first time the handle was pending and is not repeated for six hours.
3. `desk/social.py`: a new Grok job was queued even when fresh evidence was already stored. It now requeues only once the evidence is past half its hour.
4. `desk/discovery_feed.py`: after a restart the first cycle saw the previous run's feed status, read an empty feed, reported a failed fetch and slept 15 minutes. It now waits for a fresh poll.
5. `desk/ai.py`: the price history sent to Jev was the last three 30 second feed ticks, about 80 seconds and often identical rows, on 21 of 35 calls. It now takes three earlier readings spread across the hour.

Fix 2 has a side effect to know about: candidates will reach Jev sooner without Grok evidence, and at $100 those cannot be sized. That makes the first recommendation below more useful.

## What I would change

These are settings decisions for you. I have not changed any rule or limit.

1. **Leave momentum at 0.60.** It is doing its job on the candidates it is given. If you want trades, change which tokens are sent (rank by recent volume share or age instead of 24h volume) so Jev sees them before the run, not after.
2. **Stop paying Jev for candidates that cannot be sized.** When the ticket would fail the fee ceiling, skip the judge. At $100 that is every candidate without Grok evidence.
3. **Confirm the $0.95 fee floor**, then decide what the ticket rule should be for a bank under $100. As written, nothing can open below $95.
4. **Lengthen the bench after a shape or momentum reject** from 25 minutes to about 2 hours. SECRETFROG was judged 10 times, SWOLF 7 and QI 5, which is 22 of 35 calls on 3 tokens with nearly the same answer each time, while 14 tokens never got a dossier.
5. **Give "holders flat while price rises" a dead band.** It fired on a 0.0% holder change against a 0.5% price change over about 15 minutes and benched the token for 90 minutes. Requiring price up more than about 5% would remove the noise.
6. **Use a named Cloudflare tunnel** so the Grok seat's address survives restarts.
7. **Keep the Mac awake** while the desk runs. Both long gaps were the machine sleeping.

I would leave the free cut floors, the top wallet limit and every freshness limit as they are.
