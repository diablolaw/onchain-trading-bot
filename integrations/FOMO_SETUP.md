# Connect Chrome to the local FOMO market bridge

The adapter is prepared locally but has not been verified against your logged-in
FOMO session. It only calls the market-data endpoint. It does not submit trades.
Chrome attaches Privy cookies directly to market requests; their values are
never stored by the adapter. Any observed Bearer authorization stays in Chrome
session memory across service worker suspension; only allowlisted market metrics reach Python.

1. In the project terminal, run:

   ```sh
   .venv/bin/python -m desk fomo-setup
   ```

   Copy the local pairing key into the extension options only. Do not send it in
   chat. This key is separate from your FOMO credentials.

2. In Chrome, open `chrome://extensions`, enable Developer mode, click Load
   unpacked and select:

   `/Users/diablo/Projects/onchain-trading/integrations/fomo-chrome`

3. Open the extension's Details → Extension options, paste the pairing key and
   click Save pairing key. Reload your logged-in FOMO page.

4. Run the connection tester in one terminal:

   ```sh
   .venv/bin/python -m desk fomo-bridge
   ```

5. In another terminal, after up to 30 seconds, run:

   ```sh
   .venv/bin/python -m desk fomo-status
   ```

   Both `connected` and `session_ready` should be true. These establish the
   connection; an actual scanner request is still needed to validate response
   parsing and market data.

6. Stop the tester with Ctrl+C before starting a scanner with `--fomo`, since
   the scanner starts its own bridge on the same port. Add `--fomo` to your
   existing `ai-watch` command, retaining the same database/configuration.
   Restart any older scanner before doing this. Keep the independent risk
   terminal running.

Missing FOMO enrichment blocks the affected candidate. It does not silently
substitute another source. Chrome must remain running; after restarting Chrome,
reload FOMO. The bridge key is saved owner-only in `data/fomo-bridge.json`.

This connection is one integration step. Cloud Grok Bot seats, social evidence,
WorldMonitor health checks and the guide's complete Jev question sets remain
separate work; the legacy `ai-watch` workflow is not the complete guide setup.
