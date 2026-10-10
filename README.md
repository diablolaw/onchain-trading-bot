# Solana trading desk — recording, paper trading and manual live pilot

A Python 3.11+ CLI that discovers Solana candidates, records raw API responses and normalized snapshots in SQLite, applies deterministic entry rules, simulates fills, and independently monitors paper exits. Automated on-chain checks and Jupiter buy/sell quotes are available through the pilot configuration. An optional manually approved live execution layer adds wallet signing, transaction inspection and simulation, persistent order recovery, balance reconciliation and risk limits. Follow [the live pilot procedure](LIVE_PILOT.md) before funding or submitting an order. Continuous workers only trade on paper; live exits require an operator. Paper tools use the Python standard library and run directly from this directory; live tools additionally require the pinned `solders` SDK.

## Pilot screening: the next step toward live execution

For the Jev/Grok workflow, see [AI_DESK.md](AI_DESK.md). `ai-scan` and `ai-watch` discover GeckoTerminal Solana new pools, retain candidates in SQLite, and connect typed Jev judgments and Grok API analysis to paper trading, with bounded calls and recorded evidence. They require private API credentials and never invoke live signing. FOMO and the six cloud Grok Bot seats are not connected.

Pilot hard-filter thresholds and measurement differences are listed in [FILTER_ALIGNMENT.md](FILTER_ALIGNMENT.md). Use `ai-report` for the latest recorded cycle's measured checks, with separate PASS, FAIL, MISSING and SKIPPED statuses; `ai-decisions` retains historical model and rule decisions.

```sh
# Inspect one real mint: on-chain authorities, owner concentration and both quote directions.
python3 -m desk --config pilot.config.json --db data/pilot.sqlite check SOLANA_MINT_ADDRESS

# Check just the quote integration with a liquid pair; still no transaction submission.
python3 -m desk --db data/quote-check.sqlite quotes So11111111111111111111111111111111111111112 --amount-usdc 5

# Require automated evidence for all new paper entries; no holder export needed.
python3 -m desk --config pilot.config.json --db data/pilot.sqlite scan --limit 5
python3 -m desk --config pilot.config.json --db data/pilot.sqlite watch --interval 300 --limit 5
# In another terminal:
python3 -m desk --config pilot.config.json --db data/pilot.sqlite risk --interval 15
```

Replace `SOLANA_MINT_ADDRESS` with a real mint. `check` and `quotes` return 0 when their screen passes and 2 when evidence is unavailable or a rule rejects it. Their output always says `ready_for_live: false`: a quote screen does not establish transaction execution, sellability, or strategy profitability. Both commands save raw provider responses and normalized screening evidence in SQLite. `quotes` does not check token safety or concentration; `check` does not check market liquidity/age/volume, which are additionally enforced by `scan`.

`pilot.config.json` sets a $50 **paper** account, $5 ticket, one position and $5.25 cost-basis exposure cap. It requires automatic preflight, limits price impact to 1% per leg, slippage to 100 bps per leg, quoted swap fees to 50 bps per leg, and conservative quoted round-trip loss to 3%. Quotes must be at most 30 seconds old. These are engineering trial settings; they do not fund a wallet or enable live trading. Use a separate pilot database: changing the config does not reset an existing account.

Provider defaults are Solana's public mainnet RPC and Jupiter keyless requests. Optionally configure `SOLANA_RPC_URL` and `JUPITER_API_KEY` in the project-root `.env` or worker's environment. The CLI automatically loads that `.env`; exported variables take priority. See `.env.example` for supported keys. Authenticated RPC URLs and API keys are never stored in audit records. Provider URLs must use HTTPS; redirects are blocked to prevent forwarding credentials. Jupiter requests are spaced at least 2.1 seconds apart; RPC requests at least 1.1 seconds. Limits apply per process, so use one scan and one risk worker.

Owner aggregation fetches **all** token accounts for a mint through `getProgramAccounts`, aggregates accounts by owner, and checks that account balances exactly reconcile to mint supply. Responses are limited to 32 MiB and 10,000 accounts. Missing, oversized, frozen, duplicate, inconsistent or truncated account data blocks entry. Supply changes between requests can also reject a candidate; a future scan can retry. Providers must support mint-filtered `getProgramAccounts`; an unsupported endpoint is recorded as an evidence failure. Pool vaults, burn accounts and program-owned accounts are included, with no guessed exclusions. This conservative metric may reject tokens with large pool reserves; it does not mean a large owner is necessarily an individual whale. RPC results are trusted provider evidence, not independently verified chain proofs.

Mint and freeze authorities must both be explicitly revoked. Classic SPL tokens are supported. Token-2022 support is limited to immutable self-referencing `metadataPointer`, immutable `tokenMetadata`, and `immutableOwner` token accounts (or base-sized accounts without extensions). Unknown extensions, transfer fees/hooks, permanent delegates, confidential transfers, nontransferable tokens, CPI guards, and other unsupported features are rejected. Frozen existing token accounts are rejected even when the mint's freeze authority is revoked. Public-key inputs must decode to exactly 32 bytes.

Jupiter V2 `/order` requests omit `taker`, so they return quotes without transactions. The sell request uses the minimum buy output, and round-trip loss is recomputed from the two conservative minimum-output amounts. Responses are checked for matching mints, atomic amounts, routes, fee evidence, slippage, price impact and freshness. API error responses never approve a candidate. Raw quotes and normalized mint/owner evidence are saved in `preflights` and attached to scanner snapshots; `PREFLIGHT` decisions link to the evidence record by id.

Quotes are denominated in USDC (six decimal places); treating USDC as USD is an approximation. Reported signature/priority/rent estimates are capped at 5,000,000 lamports across both legs, but quote-only responses can report zero without a wallet. `wallet_costs_verified` remains false, and SOL network costs are **not** included in the round-trip percentage. A passing screen proves only that the quoted route meets these limits. Wallet-specific transaction construction, simulation and fee budgeting remain required before live execution. Quote-screened paper trades still use the existing fixed-cost fill model; quotes do not replace simulated fill prices.

The legacy paper configuration remains available. `--preflight` before the command enables the automated gate with an existing config; setting `require_preflight: true` in the config does the same. Legacy holder exports cannot bypass that gate. Exit workers remain independent and do not need fresh mint/holder evidence to process paper exits.

## Try the complete flow offline

```sh
python3 -m desk --db data/demo.sqlite replay examples/demo.json
python3 -m desk --db data/demo.sqlite decisions
python3 -m desk --db data/demo.sqlite report
python3 -m unittest discover -s tests -v
```

The committed fixture is synthetic. It buys at $1, holds at $1.05, and closes at $1.10 when six-hour volume decays. The two fills include fees and slippage, resulting in $1.69957920 simulated net profit. This is an accounting demonstration, not strategy validation. Replaying identical timestamps does not produce additional fills. Use a new database path for a fresh experiment. The `report` command uses current time, so historical fixture marks would be flagged stale.

## Collect real data

```sh
python3 -m desk --db data/desk.sqlite scan --limit 20
python3 -m desk --db data/desk.sqlite report
python3 -m desk --db data/desk.sqlite decisions
```

Discovery reads DexScreener's latest token profiles, filters Solana mints, and requests token pairs. It selects the pool with greatest reported USD liquidity, breaking ties by pool address. It also refreshes up to 100 previously watched mints. Discovery is a sample of profile-listed tokens, not every new Solana mint. Only pairs where the discovered mint is the base token are supported. Legacy defaults reject entries without owner evidence; scanning still records market data and rejection reasons. The pilot configuration obtains evidence automatically.

Run these in **two terminals** for continuous collection and independent exits:

```sh
python3 -m desk --config config.example.json --db data/desk.sqlite watch --interval 300 --holders data/holders.json
python3 -m desk --config config.example.json --db data/desk.sqlite risk --interval 15
```

Omit `--holders` if you only want collection and rejection logs. The holders file must exist when that option is supplied. `risk --once` performs one exit cycle. Stop either worker with Ctrl-C. Start both again with the same database to resume. All global options (`--db`, `--config`) precede the command. `scan` and `risk --once` return nonzero for a failed cycle; continuous workers log errors and retry on their next cycle. Per-token failures are recorded while other tokens continue.

## Legacy owner export contract

DexScreener does not supply owner-level holder concentration. Legacy paper mode accepts timestamped evidence from a separate indexer/export through `--holders`. Pilot mode uses automatic RPC enumeration instead and ignores these exports. Do not treat Solana's largest token accounts as largest owners: vaults and multiple token accounts require owner aggregation and documented exclusions.

Example `data/holders.json` (replace the mint and measurements with actual evidence):

```json
{
  "SOLANA_MINT_ADDRESS": {
    "token": "SOLANA_MINT_ADDRESS",
    "observed_at": 1791412000,
    "source": "indexer/export identifier and exclusion methodology",
    "method": "owner_aggregated",
    "top_owner_pct": 5.2,
    "top10_owner_pct": 27.4
  }
}
```

The percentages must use a consistent supply denominator, aggregate accounts by owner, and describe pool/burn exclusions in `source`. This file is trusted input; the desk validates structure, token binding, ranges and freshness, but cannot verify the export's methodology. Evidence older than one hour or dated in the future rejects entry. Raw normalized snapshots preserve the evidence used in every decision. The demo's owner evidence is fictional and must not be reused for real tokens.

## Rules and accounting

All thresholds are illustrative hypotheses, configurable in `config.example.json`. Defaults require $50,000 liquidity, pair age 6 hours to 30 days, $10,000 daily volume, largest owner at most 10%, and top ten owners at most 35%. Pair creation time is pool age, not token creation time. Missing or invalid measurements reject entry. Market freshness refers to local fetch time; the API does not provide an authoritative upstream price timestamp.

Approved candidates receive a fixed $25 paper ticket, subject to cash, position count, cost-basis exposure (including entry fees), and ticket/liquidity limits. Buys pay 1% adverse slippage, 30 bps fees and $0.05 fixed cost. Sells pay the same modeled costs. Monetary cash flows settle to eight decimal USD places using Decimal half-even rounding. Fees are charged separately from the ticket. P&L includes both sides' fees and slippage. The account is initialized only once; changing `initial_cash` does not reset an existing database. With the default exposure cap, fees may constrain the number of full tickets below the position-count limit.

Exit precedence:

1. 15% stop loss, measured from slipped entry price.
2. Reported liquidity below entry threshold.
3. 30% take profit.
4. Maximum holding time of 24 hours.
5. `volume_6h / (volume_24h / 4) < 0.20`.

Exits use the entry pool and do not require holder evidence. Missing or stale exit prices produce a blocked decision instead of a fabricated fill. A missing volume measurement does not trigger volume decay. Open positions with unavailable prices remain open; API failures cannot guarantee an exit. A one-hour cooldown prevents immediate re-entry after an exit.

The risk interval is a target, not a guaranteed latency: network requests are sequential, timeout at ten seconds, and use bounded retries. A conservative 1.1-second request spacing applies per worker. Large position counts or outages can exceed the target interval. Run one discovery worker and one risk worker per database. There is no background daemon installed automatically.

The simulation uses fixed costs, not executable quotes. It does not model failed transactions, price impact curves, rug-pull sellability, latency fills, or tax. Reported marked equity uses the latest observed midpoint before exit costs and flags stale marks. Do not interpret simulated exits during liquidity collapse as proof an actual sale would execute.

## Audit and persistence

SQLite uses WAL, foreign keys and a 30-second busy timeout. Each snapshot, decision, account update, position and fill is committed atomically under a write lock. Duplicate or out-of-order pool observations are recorded but cannot create another fill. Different workers serialize writes and enforce one open position per token.

Tables: `runs` (full rules and version hash), `raw_events` (API payloads and errors), `snapshots` (normalized data and holder/preflight evidence), `preflights` (on-chain and quote screening evidence), `decisions` (VET/SIZE/FILLS/RISK/PREFLIGHT actions and ordered reason codes), `positions`, `fills`, `account`, and `metadata`. The additional preflight table is created automatically in existing databases. Each decision links to its snapshot when one is available. Replay uses event time, never the wall clock, for decisions. Configuration changes affect subsequent decisions, including exits, and are recorded per run. Keep both workers' configs identical.

## Verified API contracts

- [DexScreener API reference](https://docs.dexscreener.com/api/reference): latest profiles, token pairs and pair lookup. Public access with endpoint-specific rate limits; requests have retries for HTTP 429, server errors and network timeouts.
- [Solana getTokenLargestAccounts](https://solana.com/docs/rpc/http/gettokenlargestaccounts): token-account data alone is insufficient for this desk's owner evidence contract.
- [Solana getAccountInfo](https://solana.com/docs/rpc/http/getaccountinfo) and [getProgramAccounts](https://solana.com/docs/rpc/http/getprogramaccounts): parsed mint/account evidence, context slots and mint filters.
- [Solana token extensions](https://solana.com/docs/tokens/extensions): supported extension checks and explicit rejection of unimplemented features.
- [Jupiter V2 order reference](https://developers.jup.ag/docs/api-reference/swap/order): atomic amounts, quote-only mode, slippage, percentage-point `priceImpact`, total `feeBps` and network/rent estimates.
- [Jupiter platform setup](https://developers.jup.ag/docs/portal/setup): keyless access and optional API-key authentication.

The manual execution layer is implemented; the next validation step is a small, attended mainnet USDC/wrapped-SOL round trip following [LIVE_PILOT.md](LIVE_PILOT.md). Funded simulation and settlement have not yet been verified against this wallet. Strategy validation on unseen data, migration from the legacy Jupiter transaction builder, and reliable continuous live exits remain requirements before unattended live trading.
