# Jev and Grok paper desk

The local CLI now connects deterministic Solana checks, Grok analysis and Jev judgment to the existing paper ledger. This is the first AI integration step toward the referenced desk. It does not deploy the author's six Grok Bot cloud seats, connect FOMO, trade prediction markets, or gather X/social or WorldMonitor evidence.

## Verified interfaces

Checked against official documentation on 8 October 2026:

| Service | Verified interface | Current integration |
|---|---|---|
| Jev / TypeSafe | [HTTP evaluation API](https://docs.typesafe.ai/api), typed questions and probabilities | Direct HTTPS client using `jev-latest`; actual model and usage recorded |
| Grok API | [Structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs), chat-completions JSON schema | Evidence analysis with `grok-4.7`; exact mint and cited field names validated |
| Grok Bot | [Persistent cloud computers](https://docs.x.ai/grok-bot/overview) and [user-created Bots](https://docs.x.ai/grok-bot/bots) | Not connected; an API model call is not a cloud Bot seat |
| FOMO | [Official trading app](https://fomo.family/) | No official documented order API verified; not connected |

The author's guide describes using a logged-in browser session for FOMO. This project does not extract browser bearer tokens, guess private execution endpoints, or claim to have reproduced that integration. Third-party products advertising a FOMO API are not evidence of an official trading API. A FOMO execution integration needs a separately verified interface and its own receipt/recovery tests; our Jupiter engine cannot validate FOMO fills.

## Run locally without funding

No wallet funding is needed. Both configured API keys have now passed small authenticated checks: Jev returned a valid typed answer and Grok returned a validated insufficient-evidence verdict. These checks verify connectivity and response handling, not trading performance.

Create API credentials in [TypeSafe Console](https://console.typesafe.ai/) and [xAI Console](https://console.x.ai/). A Grok Bot/SuperGrok subscription and Grok API access are separate interfaces; do not assume a subscription supplies an API key or API credits. Add `TYPESAFE_API_KEY` and `XAI_API_KEY` to the project-root `.env`, which the CLI loads automatically. A blank template has been created locally; `.env.example` is the shareable template. Exported terminal variables take priority. Supported syntax is one `KEY=value` assignment per line, optionally quoted, with full-line `#` comments; shell commands, interpolation and multiline values are not supported. Do not paste keys into chat or save them in tracked configuration files. API usage may incur charges.

Alternatively, for zsh, these commands configure terminal variables without echoing their values or placing the literal keys in shell history:

```sh
read -s 'TYPESAFE_API_KEY?TypeSafe API key: '; echo
export TYPESAFE_API_KEY
read -s 'XAI_API_KEY?xAI API key: '; echo
export XAI_API_KEY
```

Check presence and configuration without making requests:

```sh
.venv/bin/python -m desk --ai-config ai.config.json --db data/ai-paper.sqlite ai-status
```

Use a dedicated paper database and the pilot rules. One cycle:

The pilot pool-age range is 15 minutes to 72 hours, minimum liquidity is $12,000, and minimum 24-hour volume is $40,000, matching those numerical settings in the author's [later Jev guide](https://threadnavigator.com/thread/2102720919185617314/). AI discovery now uses the same GeckoTerminal new-pools endpoint for Solana, fetching two pages per cycle. This does not reproduce all of the guide's filters, other chains or FOMO enrichment. Ordinary `scan`/`watch` still use DexScreener latest profiles.

Discovered base-token mints and their first observed pools are retained in SQLite's `discovery_candidates` table, up to 500 mints. Young pools wait until the age minimum instead of consuming the market-fetch limit. Mints remain queued after leaving the fresh feed and expire beyond the configured maximum age. Eligible mints rotate by least-recent check, with reported volume/market-cap turnover breaking ties; missing market cap stays unknown and is never replaced with FDV. `--limit 5` now bounds the eligible mints scheduled for DexScreener market checks, not the number retained from discovery. Duplicate pools and mints are consolidated, and a newly created pool cannot reset an already queued mint's discovery age.

The new-pools client uses the [official public API](https://api.geckoterminal.com/docs/index.html), pinned API-version headers and at least 6.1 seconds between requests within a client. Run only one AI discovery worker per database. Two pages are a sample of indexed pools, not exhaustive coverage of every Solana launch. Snapshots retain discovery provenance separately from the DexScreener-selected trading pool; the existing age and execution screens apply to that selected pool. If discovery fails, output explicitly marks `fetch_failed: true` and the cycle as degraded; previously queued mints may still be checked using fresh market evidence, with no fallback to token profiles.

```sh
.venv/bin/python -m desk --config pilot.config.json --ai-config ai.config.json --db data/ai-paper.sqlite ai-scan --limit 5
```

Continuous collection and AI paper entries, with independent paper exits in another terminal:

```sh
.venv/bin/python -m desk --config pilot.config.json --ai-config ai.config.json --db data/ai-paper.sqlite ai-watch --limit 5 --interval 300
.venv/bin/python -m desk --config pilot.config.json --db data/ai-paper.sqlite risk --interval 15
```

These are local processes; keep the machine awake. The cloud services answer requests, but they do not run these workers after your machine sleeps. Stop with Ctrl-C and resume using the same database. Do not run ordinary `scan`/`watch` against this AI experiment's database: those commands intentionally use only the deterministic strategy and would mix the experiment's entries.

Inspect outcomes and audit evidence:

```sh
.venv/bin/python -m desk --db data/ai-paper.sqlite report
.venv/bin/python -m desk --db data/ai-paper.sqlite ai-decisions
.venv/bin/python -m desk --db data/ai-paper.sqlite decisions
```

## Decision flow

See [FILTER_ALIGNMENT.md](FILTER_ALIGNMENT.md) for every aligned hard filter, measurement differences and the new `ai-report` rejection report. The pilot adds market-cap, daily trade-count, sell-activity and funded-owner-count checks, and sets largest-owner/top-ten limits to 5%/60%. Execution safeguards remain separate. Missing required measurements block entry and are distinguished from measured threshold failures.

1. Discover up to two pages of GeckoTerminal Solana new pools and retain their base mints in a durable queue. Refresh a bounded, rotating set of age-eligible mints through DexScreener. Cheap market checks precede owner enumeration and round-trip quotes. All AI entry commands force automatic preflight. Cycle output includes the discovery source and fetched, retained, eligible and scheduled counts.
2. Deterministic vetting must pass. Missing authorities, unreconciled ownership, unsafe features, stale evidence, excessive concentration or quote costs cannot be overridden by either model.
3. Require at least two market observations for the same mint and pool within one hour. Cold starts record a warmup snapshot without calling AI. Code computes price/liquidity changes and the six-hour volume ratio. No holder-growth, social or world-context evidence is currently collected; those fields remain explicitly null.
4. Grok analyzes supplied evidence only. It has no search tools, wallet, shell or order tools. Its response is constrained to `consider`, `reject` or `insufficient_evidence`, bound to the exact mint and available evidence fields.
5. Jev receives the dossier and Grok analysis in one typed call per considered token. Code requires a supported shape, sufficient Choice probability and confidence, and a separate absolute Noul entry gate.
6. Jev selects at most one exact mint from the shortlist, with an explicit `NONE` option and a separate absolute worth-trading gate. Symbols are never identifiers. Selecting a relative winner alone cannot create an entry.
7. The ordinary paper ledger rechecks fresh deterministic evidence and atomically applies position, cash, exposure and sizing limits before filling. `selected_token` is a model selection; `paper_buys` reports whether a fill actually occurred. RISK remains deterministic and independent of AI.

The model thresholds in `ai.config.json` are experimental engineering settings. They are not measured probabilities of making money. A model's confidence is not a substitute for a strategy backtest or out-of-sample results. Two observations are only a warmup requirement, not proof of an edge.

## Bounds, audit and failures

Default budget: up to three considered candidates, at most seven billed request attempts per cycle, 24,000 bytes of state per request, and 700 maximum Grok output tokens per call. Provider retries are disabled for AI calls to keep attempts bounded. The call cap is not a dollar spending cap: model prices, input-token usage and run duration determine actual cost. Set provider-side spending limits. Continuous watch intervals must be at least 60 seconds; the documented default is 300 seconds.

An AI failure blocks all new entries for that cycle. A deterministic rejection, missing history, provider refusal, truncated output, invented evidence field, invalid distribution, low confidence or `NONE` selection cannot produce an entry. Earlier paper positions still require the independent risk worker. A lengthy collection/model cycle can expire a quote or market snapshot; the final ledger check rejects that entry. Do not widen freshness limits merely to force a fill.

SQLite records policy versions and full policy JSON in `ai_cycles`, typed judgments and selection in `ai_decisions`, and request/response bodies in `raw_events`. Snapshot evidence links to its AI decision. Actual returned model IDs, distributions and token usage are retained. Request headers and API keys are excluded. Exceptions are recorded by type without arbitrary provider exception text. Market/evidence payloads are sent to the configured AI services; wallet keys and private wallet data are not part of these requests.

## Remaining work before matching the post

Collect enough paper outcomes to compare the AI selector with deterministic screening alone, including fees, rejected opportunities and failures. Next integrations are sourced X/social evidence, WorldMonitor context, and supervised cloud Bot handoffs if those seats are desired. Prediction markets, other chains and a verified FOMO execution path remain separate work. Existing manual Jupiter live orders are not wired to AI decisions, and the live kill switch remains unchanged.

### Discovery and ranking comparison (paper only)

Stop the existing `ai-watch` with Ctrl-C and restart that worker with:

```sh
.venv/bin/python -m desk --db data/ai-paper.sqlite --config pilot.config.json --ai-config ai.config.json ai-watch --compare-discovery --search-query pump --search-query raydium --limit 5 --interval 300
```

Keep the existing paper risk worker running. Do not run two AI watch workers against the same account.

In another terminal, inspect the comparison:

```sh
.venv/bin/python -m desk --db data/ai-paper.sqlite discovery-report
```

This records two sampled streams: the existing GeckoTerminal queue and DexScreener search. `--limit` applies **per stream**; the deduplicated union can collect up to twice that many tokens, but AI call limits and paper position limits remain unchanged. Search queries are assumptions, not queries supplied by the author. Repeat `--search-query` up to five times. Search results are bounded and retained in a separate 500-token queue, aged and rotated across cycles. Shared tokens are labelled with both sources when scheduled by both streams. Endpoint failures mark the cycle degraded; an empty search is not proof of no launches.

Both streams receive the same deterministic preflight. Ranking prioritizes comparable owner-count growth, warns when price growth outpaces holders, and otherwise falls back to buy/sell ratio. Flat/falling holders while price rises, and sells exceeding buys in two observations, block entry. Historical holder evidence must match the mint, pair, aggregation method and source within one hour. Rolling hourly transaction windows overlap; these are successive observations, not independent windows. Cheaply rejected tokens skip expensive owner enumeration, so holder growth may be unavailable. Missing counts remain unknown.

`discovery-report` includes latest ranks, source membership, fallback flags, rejection reasons and historical pass counts. It also measures available price changes 60–90 minutes after passing observations on the same pair. These are descriptive prices, **not returns**: they exclude fees/slippage, repeat tokens and omit missing follow-ups. One shared paper account means this is not an independent portfolio experiment. A profitable-strategy claim would require isolated portfolios and complete follow-up accounting.

This is a documented approximation of SCAN, not exact reproduction of the post. It does not add WorldMonitor, FOMO execution, Grok Bot cloud seats, prediction markets or the author's private search universe. The scanner keeps the existing five-minute cadence; it does not claim the post's 30-second cadence.

### Updated batch funnel and post-aligned paper concentration

The preceding comparison instructions remain valid. `--market-limit 100` is now the default per stream; `--limit 5` is a **dossier cap**, additionally capped at three by the AI policy. The queue can collect substantially more market observations without increasing the AI budget. Comparison output includes market observations, market passes, dossiers collected and dossier deferrals. Deferral is not a measured safety failure.

`pilot.config.json` selects token-account concentration, matching the guide's Solana calculation. Its chain requests avoid complete holder enumeration and use indexed holder count when available. Missing indexed count is skipped, explicit low count blocks. Largest accounts—including potential vaults—remain in concentration and their identities are recorded. No unverified vault is removed. Transfer-fee validation is enabled in the AI paper path only. See `FILTER_ALIGNMENT.md` for the measurement and remaining differences.

The public RPC can return 429. The paper cycle shares a rate-limited RPC client; for reliable operation, add a provider's HTTPS mainnet endpoint to `.env` as `SOLANA_RPC_URL`. Do not paste its credential-bearing URL into reports or chat. A provider failure continues to block admission.

Restart the AI worker after code/config changes. Existing reports remain historical; `rules_version` changes with the concentration setting. The current paper account is preserved.
