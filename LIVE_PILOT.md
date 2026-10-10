# Manual mainnet execution pilot

The scanner, SQLite recorder, deterministic vetting and paper trading are implemented. The optional live engine now prepares, inspects and simulates transactions, signs only an explicitly approved order, persists its signature before sending, and reconciles finalized transactions with wallet balances. It is an attended engineering pilot. There is no continuous live exit worker, and these checks do not establish a profitable strategy or guarantee sellability.

## Current state

An isolated wallet has been created locally. Its public mainnet address is:

```text
3neVFoLK9Y2AEYp3mncKmfRUv9iM14e9wfp6CpiXMfsU
```

The last mainnet check found zero SOL and zero USDC. The entry kill switch is on. No real transaction has been signed or broadcast. Public quote requests, an actual unsigned Jupiter transaction and its lookup table were checked successfully. Preparation against this empty wallet stopped at the SOL reserve check. Funded simulation and the first finalized round trip remain unverified.

The local private key is an owner-only file under `data`; it is ignored by Git. Back it up privately before funding. Never paste the key into a chat or commit it. The key is stored locally, without hardware-wallet protection. Keep the same wallet and `data/live.sqlite` journal throughout the pilot. Do not delete/reset that journal, use a second journal for the wallet, or trade from that wallet through another application: recovery depends on its complete history and matching balances.

## Install and check

Run from the project directory. The current workspace already has the optional SDK installed in `.venv`. For a fresh installation:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[live]'
```

Check the public address and mainnet balances:

```sh
.venv/bin/python -m desk --db data/live.sqlite wallet-info
.venv/bin/python -m desk --db data/live.sqlite wallet-status
```

For the first engineering trial, the operator can fund the isolated address with **10 USDC on Solana and 0.02 SOL**. Verify the destination address independently in `wallet-info` before transferring. The software does not fund this wallet. Buying requires at least 5 USDC and 0.011 SOL to reserve the configured maximum costs for both legs plus the minimum SOL balance.

`live.config.json` allows a 5 USDC ticket, one position, and 5 USDC cost-basis exposure. It caps wallet funding at 50 USDC and 0.03 SOL separately; this is not a combined $50 fiat cap. Each order allows at most 0.003 SOL in observed wallet cost, including account creation, with priority fees capped at 0.0001 SOL. Slippage is capped at 1% and quoted price impact at 1% per leg. A 2 USDC daily gross realized loss or 0.005 SOL daily network-cost budget blocks new entries. These are entry controls, not a guaranteed maximum loss: an open token can lose its entire value. SOL costs are journaled separately from USDC trade P&L.

## First attended round trip

Use the same global configuration options for every prepare and submit. All global options precede the command. After funding and checking balances, enable entries deliberately:

```sh
.venv/bin/python -m desk --db data/live.sqlite live-kill-switch off
.venv/bin/python -m desk --config pilot.config.json --live-config live.config.json --db data/live.sqlite live-prepare So11111111111111111111111111111111111111112 --execution-test
```

Preparation does not sign or broadcast. It returns an order ID, approval hash, amounts, minimum output, fee limits, simulation effects and expiration. Review that concrete result. The `--execution-test` exception applies only to canonical wrapped SOL and tests execution mechanics independently of memecoin age and concentration rules. It retains quote, wallet, transaction and risk checks; it does not qualify a strategy or relax screening for other assets.

The operator submits a reviewed order with its exact hash:

```sh
.venv/bin/python -m desk --config pilot.config.json --live-config live.config.json --db data/live.sqlite live-review ORDER_ID
.venv/bin/python -m desk --config pilot.config.json --live-config live.config.json --db data/live.sqlite live-submit ORDER_ID --approve APPROVAL_HASH
.venv/bin/python -m desk --config pilot.config.json --live-config live.config.json --db data/live.sqlite live-recover ORDER_ID
.venv/bin/python -m desk --db data/live.sqlite live-status
```

Orders expire after 30 seconds, including the checks before submission. If a never-submitted order expires, cancel it with `live-cancel ORDER_ID`, then prepare and review a fresh order. The software never automatically approves, rebuilds or resubmits a transaction. Changing the rules, limits, wallet accounts or approved data invalidates submission.

Repeat read-only recovery until the buy is finalized and reconciled. Then prepare the full position exit:

```sh
.venv/bin/python -m desk --config pilot.config.json --live-config live.config.json --db data/live.sqlite live-prepare So11111111111111111111111111111111111111112 --side sell
```

Review, submit with the new exit approval hash, and recover that exit using the same commands. Confirm there are no open positions or unresolved orders, inspect both receipts and actual SOL costs, and re-enable the entry kill switch:

```sh
.venv/bin/python -m desk --db data/live.sqlite live-kill-switch on
```

The kill switch blocks entries; reviewed exits remain permitted subject to wallet, transaction, slippage and per-order cost checks. Paper `risk` commands do not monitor or close live positions. Account-creation rent counts conservatively toward SOL cost. Automatic token-account closure and rent reclamation are not implemented.

## Recovery and integration limits

Only one active intent is allowed per journal wallet. A send timeout or ambiguous response leaves the durable signature pending; use `live-recover`, which never broadcasts. Do not cancel it or prepare a replacement. Recovery checks finalized transaction contents, token deltas, fees and current wallet balances before atomically updating the live ledger. A failed transaction records its fee without inventing a fill. Expiry requires chain evidence that the blockhash has expired and unchanged wallet accounts. Unexplained balance changes stop the journal in `reconciliation_required` and turn on the kill switch. Investigate the signature and evidence; do not erase the database to bypass the lock.

The builder currently uses Jupiter Swap API V1 with inspectable V6 `route` and `shared_accounts_route` instructions. Jupiter marks [Swap V1](https://developers.jup.ag/docs/api-reference/swap/v1/swap) as superseded and no longer actively maintained. Unknown instruction formats and programs fail closed. Migration to the current builder is required before unattended operation. V2 quote-only screening remains separate. Provider RPC responses are trusted evidence; use a reliable mainnet endpoint with finalized transaction history, configured through `SOLANA_RPC_URL` when needed.

The isolated wallet must have no unrelated positive token holdings, delegated or frozen accounts. The protected-account inspection has a small-wallet limit. Token-2022 support remains restricted to the documented safe extensions. Native SOL uses the [canonical wrapped-SOL mint and reserve accounting](https://solana.com/docs/tokens/basics/sync-native); inconsistent wrapped balances reject execution. Unsupported routes or provider errors stop preparation rather than bypassing inspection.

After the first funded round trip, review actual receipts and recovery behavior before increasing activity. Continuous live exit monitoring, operational alerting, builder migration and strategy validation on unseen data are still outstanding.
