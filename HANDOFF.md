# Maddy Desk: handoff state (13 Nov 2026)

Anyone continuing this work, on any model, can start from here.

## Live
- Engine: `scripts/publish.py` (Yahoo Finance data, no AI) renders PNG pages and a PDF per slot, updates `docs/` (GitHub Pages web app) and posts to Telegram.
- Runs: 08:45, 09:50, 16:15 IST on weekdays and Saturday 10:18. Four Claude scheduled tasks push `trigger/slot.txt`, which starts `.github/workflows/nse-desk.yml`. GitHub timers remain as backup.
- Guards: one post per slot per day (`docs/data/state.json` `sent`), and a time window per slot so a late timer publishes nothing.
- Telegram secrets: `MDESKBOTTKN`, `MDESKBOTID`.
- Web app: `?only=pre|intra|post|weekly` shows one slot plus the guide.
- Odds tables: `scripts/base_rates.py`, rebuilt on Saturdays.

## Not included here
Option chain, OI, PCR, IV, Greeks, FII/DII flows and GIFT Nifty. Broker data must not be published to this public repo.

## Trading OS (local app at D:\CLAUDE\LocalCL\WEBAPP)
- Applied earlier: four MARKET DESK tasks (weekday configs), an embedded-page `MarketDeskPanel.jsx`, one branch in `Execution.jsx`. Originals in `backend/backups/market-desk-2026-11-13/`.
- Built and tested on 13 Nov, delivered to the user as `trading-os-market-desk-and-options.zip` (not written by Claude; the PC link failed):
  - native responsive `MarketDeskPanel.jsx` showing one slot per task, `lib/marketDesk.js` and test, task times 08:55 / 10:00 / 16:25 (weekly Monday 08:30);
  - options desk: `backend/app/options_desk.py`, route `GET /api/market/options-desk?instrument=NIFTY|BANKNIFTY`, `tests/test_options_desk.py`, `OptionsDeskPanel.jsx` (PCR OI/volume, max pain, OI walls and change, futures build-up, ATM IV and percentile, skew, Greeks, straddle range, model probabilities). State file `backend/state/options_desk.json` holds the OI baseline and IV history.
- Backend suite on the copy: 243 passed. User applied the zip on 13 Nov; OI stats confirmed working in the app.
- 13 Nov, 21:00: task page redesigned and written directly to the PC: verdict strip (per-index Bullish ... Bearish from six voting signals, plus a Risk-on / Neutral / Risk-off regime), tabs Overview / Options / Sectors / Stocks / Guide, compact tiles in the PREMARKET_MACROS style (`components/deskUi.jsx`). Verdict logic and tests in `lib/marketDesk.js`. Previous versions in `backend/backups/market-desk-2026-11-13-v2/`.

## Manual run
Actions > nse-desk > Run workflow. Slot: pre, intra, post, weekly or rates. Add `--force` to run outside the slot window or to post again on the same day.

`scripts/desk.py` is an unused earlier draft.
