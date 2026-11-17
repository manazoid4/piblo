# Premium homepage + real demos + App Studio (17 Nov 2026)

## What changed
- **Homepage** (`site/index.html`, `site/premium.css`): dark bronze / warm white look, founder's Home concept as the product-first hero (the Cardputer-in-a-case render is removed from the repo). Sections: real runs, why not your phone, Buddy, App Studio, Batch 01, build notes, FAQ, founder.
- **Batch 01 reconciled everywhere**: Piblo Standard + Home bundle, £129 early price. £5 reservation is refundable, separate, not taken off the price, and gives priority access + the early-price offer. Removed £149 Standard-only copy and the "£5 counts towards it" line on `/reserved/`.
- **App Studio** (`site/studio/`): three working mini-apps (Focus timer, Tally, Tonight list) on a Home-style preview with BACK / DEL / YES keys, live editing and "Copy app file". Pure logic in `apps.js` with tests.
- **Core: two-step voice requests** (`host/mazhost/compound.py`, `compound_run.py`, wired in `app.py`): "save the spreadsheet I'm working on and remind me to finish it tonight" saves the foreground window with Ctrl+S (only if it is still the same window) and sets a Promise Keeper reminder, after one YES on the existing approval queue. Steps stop at the first failure. Low-risk pairs (focus + reminder) run without approval. `GET /compound/last` reports the latest run. Promise Keeper now understands "tonight" (20:00 default).
- **Demo recorder** (`host/scripts_demo_run.py`): records both demos against the real Core routes, real PC controller and a real OpenOffice Calc window, with temp data (never the live Core).

## Honest limits
- The handheld keypress was not part of the recorded run: the script calls the same `/buddy/decide` route the Cardputer calls. A run with the physical device needs Maz (no flashing needed; firmware already shows approval cards).
- Piblo Home hardware, the 4–5" screen and front dock are concepts.
- Installing App Studio apps on a device is not built.
