# Deterministic filter alignment

The pilot now implements the Solana numerical hard filters in the author's [later Jev guide, section 3](https://threadnavigator.com/thread/2102720919185617314/), checked on 9 October 2026. These are copied experimental thresholds, not validated profitability claims. They apply when running with `--config pilot.config.json`; legacy defaults remain different.

| Filter | Guide | Pilot | Measurement / qualification |
|---|---|---|---|
| Pool age | 15 minutes–72 hours | Same | DexScreener selected-pool creation time; GeckoTerminal discovery age also gates queue scheduling |
| Liquidity | At least $12,000 | Same | Selected pool's reported USD liquidity |
| Daily volume | At least $40,000 | Same | Selected pool's reported trailing 24-hour volume, not an extrapolation |
| Market cap | $60,000–$8 million | Same | DexScreener `marketCap`; missing remains unknown, never substituted with FDV |
| Daily trades | At least 150 | Same | Exact sum of reported 24-hour buys and sells; both must be present, nonnegative integers |
| Sell activity | Reject zero hourly sells with more than 20 hourly buys | Same | Reported hourly buy and sell counts; 20 buys is allowed, 21 with zero sells rejects |
| Largest wallet | At most 5% | Threshold matches; measurement differs | Our complete enumeration aggregates all funded token accounts by owner. The guide's Solana snippet uses the largest token account |
| Top ten holders | At most 60% where available | 60% of aggregated owners | No guessed exclusion of pool vaults, burn addresses or program owners |
| Holders | At least 80 where available | 80 funded owners | Our count is distinct owners with positive balances after supply reconciliation, not number of token accounts |
| Mint/freeze authorities | Reject active authorities | Same | Explicit revoked fields required; missing does not mean revoked |

## Evidence handling differs deliberately

The guide skips some absent chain fields; this pilot blocks entry when required ownership or authority evidence is unavailable. It reports that as **MISSING**, not a measured threshold failure. This preserves the existing requirement for complete, reconciled ownership evidence. Owner aggregation differs from largest-account concentration and can be stricter for owners with multiple accounts. Vaults remain included, so the 5% threshold can reject legitimate pools. The copied threshold is not evidence that every rejection is a scam.

The guide uses FOMO enrichment and other chains. This implementation uses GeckoTerminal discovery and DexScreener market data on Solana. Data from different providers can give different measurements even with identical numerical limits. FOMO, EVM contract checks and social research remain unconnected. AI prompts and confidence gates have not been replaced with the guide's soft-filter system in this change.

## Additional execution safeguards

The report labels these separately as `execution_safety`: fresh market/evidence timestamps, supported token features, full owner/supply reconciliation, and round-trip quote checks for mints/amounts/routes, freshness, expiry, slippage, fees, price impact and conservative loss. Existing paper cash, position, exposure and ticket/liquidity limits still apply after screening. The AI layer may also reject or wait for history. Passing hard filters never guarantees a fill or execution readiness.

## Rejection report

After restarting `ai-watch` to load the changes, run in another terminal:

```sh
.venv/bin/python -m desk --db data/ai-paper.sqlite ai-report
```

This displays the latest cycle with recorded filter reports, not the latest 100 historical AI decisions. Each check includes its name, group, observed value, required threshold and status:

- **PASS**: available evidence meets the check.
- **FAIL**: available evidence violates a check.
- **MISSING**: the required measurement is absent or invalid; not a claim that an authority is active or a threshold was exceeded.
- **SKIPPED**: expensive preflight was deliberately not requested after early market rejection.

The report also shows entry-blocking reasons and evidence collection errors. No matching DexScreener pair gets a missing-market report rather than silently disappearing. A specific old run is available with `ai-report --run-id RUN_ID`. Reports store original measurements, status and rule version in SQLite's `filter_reports`; changing configuration does not reinterpret old records.

Old decisions from before this feature do not gain invented measurements or rewritten verdicts. The first report becomes available after a new candidate is checked. Discovery-only cycles with no scheduled candidates may have no filter rows. Cycle output now includes `rules_version` as well as the AI `policy_version`.

## 9 October follow-up: corrected Solana funnel

The pilot's `concentration_measure` is now `token_accounts`. The paper path calls `getTokenLargestAccounts` and `getTokenSupply`, validates mint/supply identity, slots, decimals, duplicate addresses and the distribution bound, and records the largest account identities. The largest account and top-ten-account percentages are explicit, rather than presented as aggregated wallet shares. Full owner aggregation remains the legacy default. Largest-account evidence is a partial distribution; its supply verification is **not** full holder reconciliation. This removes the requirement to enumerate every account merely to reproduce the guide's concentration calculation.

**The post does not exclude liquidity vaults in that Solana calculation.** We therefore retain them and record their addresses. An account exceeding 5% is a concentration rejection, not proof that a trader owns it. Removing an unverified vault would contradict this measurement and can hide risk. Vault classification remains unimplemented; no guessed exclusions are applied.

Paper-only transfer-fee support validates the Token-2022 fee configuration, both fee schedules, bounds, epochs, authorities and withheld amounts. Full enumeration, when used, includes withheld mint/account fees in reconciliation but never counts them as spendable owner balances. The paper path records fee configuration and uses fresh buy/sell quotes to enforce actual round-trip limits. Fee authorities may remain active: quote evidence describes current conditions and does not prove future fees immutable. Other unsupported extensions still block. The live engine has not opted into this support.

Indexed holder count comes from GeckoTerminal token info. When absent, the post-aligned account mode marks the holder count check skipped, as the guide does; it does not infer a total from the top twenty accounts. A supplied low count still rejects. Market and indexed data are provider evidence, not proof of chain state.

The scanner now defaults to up to 100 market candidates per stream, fetched in DexScreener batches of at most 30. Market limits are separate from dossier limits. Known market-qualified GeckoTerminal candidates get scheduling priority, and turnover orders collected candidates before at most three dossiers. Cheap failures are still recorded. The experimental search stream remains a secondary sample; `pump`/`raydium` searches are not a comprehensive token universe. This improves admission coverage rather than weakening thresholds.

Quotes are refreshed after dossier collection for otherwise eligible candidates. The selected token's same-pair market and preflight evidence are refreshed after AI latency, and failure blocks its paper entry. Real freshness checks stay enabled. Ranking uses evaluation time and no longer converts every preflight failure into an early market rejection.

Still unmatched: FOMO logged-in batch enrichment, other chains, the guide's full typed question sets/thresholds, official-project X research, WorldMonitor, Grok Bot seats, prediction markets and FOMO execution. These changes align the Solana collection/concentration mechanics; they do not reproduce the full external desk or establish profitable selection.
