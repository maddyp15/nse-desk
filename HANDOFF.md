# Maddy Desk: handoff state (13 Nov 2026)

Anyone continuing this work, on any model, can start from here.

## Live
- Engine: `scripts/publish.py` (Yahoo Finance data, no AI) renders PNG pages and a PDF per slot, updates `docs/` (GitHub Pages web app) and posts to Telegram.
- Runs: 08:45, 09:50, 16:15 IST on weekdays and Saturday 10:18. Four Claude scheduled tasks push `trigger/slot.txt`, which starts `.github/workflows/nse-desk.yml`. GitHub timers remain as backup.
- Guards: one post per slot per day (`docs/data/state.json` `sent`), and a time window per slot so a late timer publishes nothing.
- Telegram secrets: `MDESKBOTTKN`, `MDESKBOTID`.
- Web app: `?only=pre|intra|post|weekly` shows one slot plus the guide.
- Odds tables: `scripts/base_rates.py`, rebuilt on Saturdays.

## Not included
Option chain, OI, PCR, IV, Greeks, FII/DII flows and GIFT Nifty. These are planned inside the user's local Trading OS app, which has broker data. Broker data must not be published to this public repo.

## Manual run
Actions > nse-desk > Run workflow. Slot: pre, intra, post, weekly or rates. Add `--force` to run outside the slot window or to post again on the same day.

`scripts/desk.py` is an unused earlier draft.
