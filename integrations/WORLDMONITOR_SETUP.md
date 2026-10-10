# WorldMonitor credential setup

## Local API and refresh worker

The local API was started on `http://127.0.0.1:3000` and verified from
`desk worldmonitor-status`. Crypto quotes and fear/greed are available. Overall
readiness remains false: social/cross-source feeds are absent, prediction data is
not populated, and the crypto API does not expose a source timestamp.

The refresh worker was verified against Redis and started in this session. It
refreshes crypto every five minutes and fear/greed every fifteen minutes. These
processes are session processes, not installed services or login-start jobs.
To restart the worker after stopping the existing copy:

```sh
.venv/bin/python -m desk.worldmonitor_feeds --node /Users/diablo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
```

Use `--once` for a single verified refresh. Only sanitized results are printed;
an upstream zero exit status alone is not considered success. To restart the API,
run Node.js 24 with `node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3000
--strictPort` from `integrations/worldmonitor`.

The installed relay's social collector supports `SCRAPECREATORS_API_KEY` or
Reddit OAuth credentials (`REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`). Neither
has been configured or live-verified here. Cross-source signals require their
underlying feeds; publishing an empty aggregate is not evidence of coverage.

## Verified connection and initial feeds

On 10 October 2026 (local time), the configured Redis REST credentials passed
PING. The upstream fear/greed seeder completed and verified one record in Redis;
the crypto quotes seeder completed and verified ten records. Credentials were
not displayed. These were one-time seeds, not continuously scheduled feeds.
Fear/greed reported missing put/call and S&P breadth inputs and used its documented
fallbacks where available. Social velocity and cross-source source populations,
local API serving, and automatic refresh remain to be verified/configured.

1. Open https://console.upstash.com/ and sign in.
2. In Redis, choose Create Database. Name it `trading-desk-context`, choose a
   nearby available region, then choose a plan appropriate for your usage.
3. Open the database's details and find its REST connection credentials.
4. Edit this owner-only local file:

   `/Users/diablo/Projects/onchain-trading/integrations/worldmonitor/.env.local`

   ```dotenv
   UPSTASH_REDIS_REST_URL=https://your-database-endpoint.upstash.io
   UPSTASH_REDIS_REST_TOKEN=your-rest-token
   ```

   Use your actual REST URL and REST token. The Redis TCP URL/password connection
   string is a different interface. Do not paste credential values into chat.

The trading project's `.env` remains for Jev/xAI/Jupiter/Solana providers.
WorldMonitor's secrets go in the separate file above.

## What happens after credentials are added

We must first verify Redis connectivity with sanitized output, then prepare the
upstream data feeds and seed jobs. Required sources currently return unavailable
rather than useful context. Creating an empty database does not repair that.

The installed upstream version has these relevant seed scripts:

- `scripts/seed-market-quotes.mjs`
- `scripts/seed-fear-greed.mjs`
- `scripts/seed-prediction-markets.mjs`
- `scripts/seed-cross-source-signals.mjs`

The cross-source writer aggregates other source keys, so it cannot replace the
underlying feeds. Reddit social velocity is supplied by the relay's Reddit
collector; it is not an independent simple `seed-social` script. Providers may
require additional credentials or have public-service availability limits.

The local frontend also needs its generated inventory file and unresolved `three`
dependency addressed. The repository's preflight was inspected; it flags missing
blog-site dependencies, generated facts, and contributor/PR credentials. Those
contributor checks are not authorization requirements for your local credential
configuration. Upstream source has not been patched or submitted as a PR.

Once credentials are in place, tell the desk they are added. Verification must
not display them or claim readiness merely because an HTTP request returns 200.
