# Post implementation and remaining connections

Updated 10 October 2026. This is a paper implementation in progress, not a claim
of complete replication or live readiness. Original and later Jev workflows are
kept distinct. Your existing `data/ai-paper.sqlite` account is unchanged.

## Implemented and verified

- `discovery-watch`: independent DexScreener polling every 30 seconds, durable
  bounded launch feed and one-hour observation history. Search queries are
  configurable; the article does not publish a complete query universe.
- `ai-watch --discovery-feed`: consumes fresh observations separately from
  expensive data collection and AI calls. Sources older than 120s are excluded;
  stale/degraded discovery is reported explicitly.
- FOMO: cookie session and real response parsing verified against your Chrome
  session. Market metrics only; no venue order endpoint or cookie export.
- Post collector: FOMO free cut before DexScreener market fetch, followed by
  at most three Solana dossiers. Existing pilot collector remains available.
- `post-ai.config.json`: Jev market/chain/social sets and final absolute-worth
  gate; no Grok API veto. Numeric guide gates, typed score validation and soft
  rejection cooldowns. Chain sets for other networks are definitions only.
- `judge-serve`: authenticated loopback Jev proxy, state/body caps, serialized
  provider calls and 60 calls/hour ceiling for cloud callers. The local pipeline
  calls Jev directly with its per-cycle budget. Bots do not receive the API key.
- SOCIAL jobs and ingestion bound to the indexed project handle and timestamp.
- `post-paper.config.json`: volume-only runner exits; stated article fee rate and
  floor; 6%-capped Kelly sizing from explicit sourced probability/payoff inputs.
  Mutable metadata authority alone is accepted in this paper profile, because
  the post tests mint/freeze authority. Invalid metadata identity still rejects;
  manual live validation retains its stricter defaults.
- RISK retries missing evidence twice, then records a close intent. Settlement
  waits for a fresh price, rather than fabricating an executable close.
- `post-run`: separate SCAN, Jev decision, RISK and judge workers, supervisor
  heartbeat every minute, two restart attempts before entry workers are halted;
  RISK remains supervised independently. Ctrl+C stops children.
- Inactive workers are terminated and reaped before restart: SCAN after five
  minutes without output, RISK after ten minutes, and Jev after its configured
  interval plus fifteen minutes. The request-driven judge is exempt. This checks
  output activity, not provider success; a worker emitting errors is still active.
- `book-scan`: original BOOK public-price collection plus exact-identity matching
  against independent, sourced, fresh forecast inputs; no prediction portfolio.

The Jev integration was tested with a real small request. Actual model:
`jev-1.13.0`; 388 input / 76 output tokens. No trades were made.

## Start the new isolated paper profile

Stop your old scanner and any standalone fomo-bridge/judge service first to free
ports 8766 and 8080. Keep Chrome/FOMO open. Then, from the project:

```sh
.venv/bin/python -m desk post-run --observe-only
```

The new account is `data/post-paper.sqlite`, initially $10,000 simulated cash for new accounts.
Discovery runs every 30 seconds, Jev decision cycles every 15 minutes, RISK every
five minutes. The decision interval is independent and configurable with
`--decision-interval` (minimum 60 seconds). Existing pilot risk can keep monitoring
its old account, but never run two risk workers against the same account.

No explicit Kelly inputs are configured yet. Observation mode records judgments
without entries; entry mode requires `post-run --sizing-file PATH`. This is deliberate: the post omits the source of p/payoff, and
Jev's confidence is not a calibrated trading win probability.

For operator-supplied inputs, use `post-run --sizing-file PATH` (or the same flag
on the individual decision worker). JSON maps exact mint to `probability`, positive
`payoff`, `observed_at` (Unix seconds), nonempty `source`, and optional `locked_cash`,
`operating_reserve`, `max_fee_pct`. Inputs expire after one hour and must be refreshed
by their actual source. Startup checks current cash, partial sizing and fee viability.
The new paper ticket cap is $600, with fresh quotes at the final calculated size.
The default 3% fee ceiling is a configurable assumption. Existing $1,000 accounts
are not reset: startup will explain when their cash/fee constraints are incompatible.
No sizing probability or payoff is fabricated by the desk.

Inspect:

```sh
.venv/bin/python -m desk --db data/post-paper.sqlite ai-decisions
.venv/bin/python -m desk --db data/post-paper.sqlite ai-report
```

Free-cut rejections are separately recorded as `post:free_cut_rejections` raw
SQLite evidence; these candidates never consume a DexScreener call or dossier.

## Grok Bot connection — requires your account setup

Install/sign into Grok Bot using the official getting-started flow:
https://docs.x.ai/grok-bot/get-started
Then create the seats using `integrations/grok-bot/SEATS.md`. No bots have been
created automatically. SOCIAL requires its authenticated X plugin.

`judge-setup` prints a separate desk secret for your own bot configuration. Keep
that secret out of chat. The local service binds 127.0.0.1:8080; cloud bots cannot
reach it yet. A managed HTTPS tunnel still needs installation/configuration and
an authenticated connectivity check. Never expose the FOMO bridge publicly.

## WorldMonitor connection — currently degraded

Official source was installed at `integrations/worldmonitor`, commit
`d49477575bb0e6282cb03080f792989136818ee7`. Runtime started and endpoints were tested.
The test server was then stopped. Required social and cross-source endpoints
returned 503 because Redis seed data was absent; fear/greed and crypto quotes
also returned unavailable/degraded results. The upstream frontend additionally
has an unresolved `three` import and a missing generated inventory file. The desk does not claim these responses are
usable context.

The upstream environment names are `UPSTASH_REDIS_REST_URL` and
`UPSTASH_REDIS_REST_TOKEN`, in its own `.env.local`, separate from the trading
project `.env`. Redis alone is insufficient: the required seed jobs/data providers
must populate it. Do not put these values into chat. Step-by-step instructions
are in `integrations/WORLDMONITOR_SETUP.md`; the empty owner-only `.env.local`
file has been created for you.

Once a local service is ready, inspect `worldmonitor-status`, then start the
supervisor with `--worldmonitor-url http://127.0.0.1:3000` (or the actual local port).

## Still required for a complete reproduction

- Grok Bot cloud seats, X session/plugin and authenticated HTTPS service tunnel.
- Operational WorldMonitor data providers, Redis seeds and story verification.
- BSC/Base/Robinhood collectors and chain evidence (question definitions alone
  do not enable those assets).
- Independent forecasts/scorecards and a complete BOOK portfolio/risk workflow.
- FOMO execution, actual fee validation, partial fills and venue reconciliation.
- Defined whale-distribution precedence, one permitted retest add, and automatic
  operating-cost reserve accounting.
- Telegram destination/token and end-of-day/material-change reporting.

None of these pending items is reported as connected or live ready. Funding a
wallet is not the next dependency. The next external dependency is Grok Bot access.

Review-fix details and limitations: `POST_REVIEW.md`. All current changes remain paper-only.
