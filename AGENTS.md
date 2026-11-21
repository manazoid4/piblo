# Agents working on Piblo

## Start every session with the maintainer's memory
Maintainer agents: before planning or building, read the private Unified Memory (`projects/piblo/STATUS.md`, newest `work/piblo/`) and the private repo's `AGENTS.md`, which indexes saved build and research prompts. Reuse a saved prompt before researching again.

## See and drive the real device (do this before and after any UI change)
Piblo Core on this PC (started by the Piblo app) proxies the device's screen and keys over Wi-Fi.
No token, IP or USB is needed: the script finds Core and its token itself.

```
python scripts/piblo_remote.py status               # app, battery, charging, Wi-Fi
python scripts/piblo_remote.py screen               # prints a PNG path: open/read it to see the LCD (720x405)
python scripts/piblo_remote.py keys "down*2 enter"  # press keys, prints a fresh screenshot path
python scripts/piblo_remote.py open settings        # open an app by id, prints a fresh screenshot path
```

- Key names: letters/digits, `enter esc space tab backspace up down left right`, with `*N` to repeat.
- Every keys/open call returns a screenshot, so it's one command per step. Look at the image before claiming a screen works.
- "Core is not running" means: open the Piblo app. A 503 means Piblo is off or not connected to this PC
  (on Piblo: More -> Device & PC -> Connect to computer, then Allow in the app).
- Never print or commit the token. Flashing firmware needs Maz's explicit OK each time.

## Rules
- Before writing a firmware driver, audio pipeline, pairing flow or UI state machine, check the reference repos in `docs/REFERENCE-REPOS.md` (`bash scripts/clone_refs.sh`). Copy only from MIT/BSD/Apache code, with attribution; GPL/AGPL repos are read-only.
- The product is Piblo. Never show Nod, MAZ, Maz Core, Maz Pocket or Cardputer to users.
- Never show Maz's location beyond "UK" in anything public.
- Protocol contracts live in `docs/protocol/`. Change one only with the orchestrator, because Core, firmware, Android and the app all build against them.

Test results and screenshots go in testing/ (see testing/README.md); update testing/areas/<area>.md, never scatter evidence elsewhere.
