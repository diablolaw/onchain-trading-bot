# Fix status — 10 October 2026

Sizing/concentration follow-up: the supplied original post explicitly defines
top_wallet_percent as largest token-account balance / supply and rejects above
5%; it does not specify verified pool-vault exclusions. That conservative rule
is retained and its account-versus-wallet limitation is now provided to Jev.
The earlier $600 pre-AI quote was an implementation mismatch: post-mode collection
now defers execution quotes until Kelly sizing selects an actual ticket. Mint,
market and concentration checks still gate Jev. The market question receives a
labeled upper bound capped by configured ticket, 6% cash and 2% liquidity.
Fresh quotes at the actual size and the full deterministic vet remain mandatory
before paper entry. Reports label pre-AI screening and deferred quote checks.
All 134 tests passed, including oversized preliminary quote versus failed actual
entry quote cases. No live market success or paper trade is inferred from tests.

Follow-up: added inactivity detection and reap-before-restart for SCAN, RISK and
VET_JEV, with the idle request-driven judge exempt. Database paths are resolved
before child startup so validation and workers use the same account even when
launched outside the project directory. Seven focused review regression tests
passed after this follow-up; the preceding full suite passed 133 tests.

The findings below are the original review. Implemented fixes now include:

- Supervisor forwards `--sizing-file`; entry startup validates fresh inputs and
  fee viability. `--observe-only` is an explicit alternative. A calibrated source
  of probability/payoff remains required; no model confidence is substituted.
- New paper defaults: $10,000 simulated cash, $600 ticket cap, $605 exposure cap.
  Existing balances are preserved and validated, never silently reset.
- Non-risk failure disables entry workers but preserves RISK; a failing RISK worker
  is retried with backoff rather than abandoned along with the whole desk.
- Solana GeckoTerminal launches are fetched every five minutes alongside 30-second
  search polling; up to 500 watched mints are refreshed by address in rotating
  batches of 60, independent of continued search visibility. Multi-chain remains pending.
- Benches and held-position gates apply before market/dossier work; deterministic
  rejections now enter the bench. Holder-growth ordering precedes dossier allocation.
- Jev market rubrics are expanded, states are tailored per question set, social
  receives token narrative/published handle, and pick receives candidate evidence.
  This remains an adapted implementation, not a verbatim prompt replica.
- WorldMonitor validates timestamps and payloads and projects large responses.
  Crypto quotes without source timestamps explicitly keep context degraded.
- A pending close can settle from a fresh price while volume remains unavailable.

Validation: 133 local tests passed, including targeted review regressions. No live
orders or new paid provider requests were made for these fixes. Operational and
external integrations listed as missing below remain outstanding.

---

# Implementation review — 10 October 2026

Scope: current local code against the original supplied Grok Bot article and the
later Jev guide already reviewed in this conversation. Review only: trading code
and settings were not changed. This is not a profitability assessment.

## Findings, in priority order

### P1 — Default post-run has no path to a paper entry

`desk/supervisor.py:15` launches the decision worker without `--sizing-file` and
provides no option to forward one. `desk/post_evaluate.py` rejects a selected mint
without those explicit inputs. Independently, the default fee check prevents any
entry at the configured size: maximum $60, two $0.95 floors, default 3% maximum
round-trip fees. Even perfect probability and ample liquidity return zero size:
3.1667% fees at $60, or 5.2778% at the $36 partial-evidence size. These were reproduced
by calling the actual size function. More waiting, better candidates or Upstash
credentials cannot resolve these configuration contradictions.

Fix: expose the sizing source through the supervisor, define how edge inputs are
produced, and validate account/ticket/fee settings together before starting. The
post leaves some sizing inputs unspecified; do not substitute Jev confidence.

### P1 — A failed non-risk worker shuts down RISK too

`desk/supervisor.py:38` returns after repeated failures in any seat. Its finally
block at line 43 terminates every remaining worker, including RISK. An occupied
judge port or repeated scanner failure can therefore stop monitoring existing
positions. This is currently a paper-account issue and must be fixed before live
use. Stop new entries on failure while maintaining exits/monitoring and surfacing
an explicit operational alarm.

### P1 — New post-run narrows discovery instead of reproducing the later guide

The supervisor always selects `--discovery-feed`. `desk/discovery_feed.py:29` only
polls configured search queries; default queries are pump and raydium. This path
bypasses GeckoTerminal discovery entirely and rejects non-Solana results. The
later Jev guide uses launch pools across Solana/BSC/Robinhood. The original's 30s
cadence is implemented, but its full candidate universe was never specified.

Additionally, retained tokens are not refreshed by address: only current search
results update them, and candidates older than 120s are excluded (line 55). A
new pool disappearing from search cannot simply mature in this feed and later
qualify. The previous durable Gecko queue does not help in this mode.

Fix: make the target profile explicit, retain launch discovery and refresh watched
mints independently of search visibility; then add the intended networks.

### P2 — Cooldowns and open-position checks happen after expensive collection

`desk/ai_cli.py:261` spends up to three dossier slots before `post_evaluate.py:22`
checks existing positions and line 24 checks the bench. The same high-turnover,
benched candidates can keep consuming those slots and defer other candidates.
Deterministic hard rejections do not populate post_bench at all. The advertised
rejection cooldowns therefore cover only part of the pipeline.

Fix: exclude benched candidates and skip new-entry collection while holding before
allocating FOMO/dossier/AI work; apply reason-specific cooldowns at rejection time.

### P2 — WorldMonitor can accept empty context and discard useful large responses

`desk/worldmonitor.py` checks serialized size, then rejects anything over 1024 bytes
instead of selecting relevant fields using the post's projection step. Conversely,
`{posts: [], fetchedAt: 0}` is marked available for the social endpoint; reproduced
with its actual context method. There is no general source timestamp freshness
check. World claims are not independently cross-verified; attaching a context object
to a Jev state does not implement that VET rule.

Fix: endpoint-specific schema, timestamp and availability validation, bounded
projections, and explicit evidence linking for each claimed story.

### P2 — Jev matching is approximate, not exact

`desk/post_questions.py` replaces detailed guide rubrics with much shorter ones.
Matching field names and thresholds does not preserve model behavior when rubric
semantics change. The final pick receives judgment summaries rather than full
candidate evidence. Social state lacks the token narrative supplied in the guide.
Chain and market calls receive a broad shared dossier rather than tailored states.
Holder-growth ranking happens only after dossier allocation, and sell dominance
compares overlapping h1 observations rather than explicitly distinct time windows.

Fix: audit each question, input field, unit, rubric, threshold and fallback against
the intended guide version. Record deliberate deviations separately.

### P2 — Pending close can remain blocked when only volume is unavailable

After retry exhaustion, a close intent is recorded. On later polls,
`desk/cli.py:40` still requires both volume fields before calling desk.record.
Consequently, a fresh price alone cannot settle that pending close despite the
setup document implying it can. A usable executable price is necessary, but volume
should not be required after an unconditional close has already been requested.

## What is actually verified

- FOMO Chrome session and live market response, verified using the user's output.
- Jev HTTP connection and typed score response with one real small test request.
- SQLite recording, deterministic validation and paper ledger functionality.
- Code for independent 30s discovery, separate decision cadence, post exit policy,
  fee floor, sizing arithmetic and local social/judge endpoints.
- Previous full suite: 127 tests passed. This review used targeted reproductions;
  that test count is not proof of complete source alignment or integrated operation.

## Still absent or only scaffolded

- Grok Bot cloud seats: instruction file only; no actual bots, X login or tunnel.
- Only SOCIAL has a remote evidence queue. Other cloud seats cannot operate the
  complete local pipeline through the current service API.
- WorldMonitor's required seed data/providers; Redis credentials alone are insufficient.
- Multi-chain discovery, evidence and paper execution; non-Solana question sets
  exist but do not create a working multi-chain path.
- FOMO venue execution, venue quotes, partial fills, settlement/reconciliation.
  Current paper valuation uses DexScreener and admission uses Jupiter quotes.
- Full original BOOK workflow: discovery/forecast scorecards, independent forecasts,
  portfolio accounting, seven-book limit, resolution and edge-loss exits. book-scan
  is a public-data/evaluation adapter, not that workflow.
- Automatic operating-reserve accounting, retest add, whale-distribution logic,
  Telegram/end-of-day reporting, and detection of alive-but-stuck workers.

The application remains a partially integrated Solana paper desk. The two posts
have different architectures; the current post profile still mixes them. Fix the
entry blockers, risk supervision and discovery loss before adding more integrations.
