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

## 2026-11-14 changes
- Slot content (`SLOT_CONTENT` in scripts/publish.py): pre = index plan only (one image, sent with sendPhoto);
  intra = plan + early volume shockers; post and weekly = plan + sectors, breadth, industries, stock lists.
- Page one of every slot is the overview: verdict strip (Python port of the Trading OS verdict and risk
  regime), index strip, plan cards, timeframes and global cues. Text widths are measured with the installed font.
- Duplicate check includes the date, so Saturday's weekly posts even though it uses Friday's bar.
- `trigger/slot.txt` containing `<slot> refresh` rebuilds that slot's pages with --force --no-telegram.
- docs/index.html is rebuilt in the same light layout, one tab per slot.
- Trading OS (D:\CLAUDE\LocalCL\WEBAPP, private, never published here): PREMARKET_MACROS merged into the
  pre-market desk task (id `premarket_macros` kept, 08:55, desk_view pre); market_desk_pre removed; tick-lists
  removed; monthly CPR moved to the intraday desk; new endpoints /api/market/index-contribution,
  /api/market/fno-buildup, /api/market/monthly-cpr (backend/app/market_internals.py); options desk covers SENSEX.
  Backups: backend/backups/market-desk-2026-11-14/.

## 2026-11-16
- NSE moved to nseindia.in; index members now come from /api/NextApi/apiClient/marketWatchApi?functionName=getIndicesData
  (old addresses kept as fallbacks) in Trading OS backend/app/market_internals.py.
- Options desk and internals go to Telegram from Trading OS itself (backend/app/desk_report.py) at 10:00 and 16:25,
  as two images and a PDF, because the GitHub publisher has no broker data. Needs Pillow in the backend venv and
  MDESK_TELEGRAM_TOKEN / MDESK_TELEGRAM_CHAT_ID in backend/.env. Status and "Send now" sit on the Internals tab.

## 2026-11-17 incident
- The four Claude scheduled tasks had been removed and GitHub's timer was hours late, so pre-market did not publish.
  Recreated the tasks (Haiku; each now confirms the slot published). `trigger/slot.txt` with `<slot> force`
  runs and posts outside the slot window (used to catch up pre at 09:19).
- Backup trigger in Trading OS (backend/app/desk_trigger.py): a few minutes after each slot it checks
  docs/data/<slot>.json through the GitHub API and pushes the trigger if today's run is missing.
  Needs MDESK_GITHUB_TOKEN (fine-grained, nse-desk only, Contents read/write) in backend/.env.

## 2026-11-20
- F&O engulfing lists (`engulfing`, `engulf_lists` in scripts/publish.py): daily in post, weekly in weekly.
  Body of the last candle fully covers the prior body, opposite colour, volume >= ENGULF_MIN_VOL (1.5) x the
  20-day / 10-week average. List keys fno_bull_engulf / fno_bear_engulf; columns eng (body x) and pre (prior 5 bars %).
  F&O symbols: NSE fo_mktlots.csv, fallback config/fno.csv (218 stocks from Kite futures on 2026-11-16).
