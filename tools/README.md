# tools/ - drive and measure the physical Piblo (stdlib + pyserial; wraps scripts/piblo_remote.py)

All commands talk to the real Cardputer through Piblo Core (token found like piblo_remote.py). Each has `--help`.

| Command | What it does | Physical or simulated |
|---|---|---|
| `python tools/piblo_live.py --auto` | status line + browser preview at 127.0.0.1:8799 (polls Core screen PNG) | Physical when a device answers (page says "PHYSICAL DEVICE via Core"); else "SIMULATED" black frame |
| `python tools/piblo_capture.py --screen current\|<app>` | saves `<timestamp>-<app>.png` (opens app, then Esc home) | Physical |
| `python tools/piblo_navigate.py --screen <app> \| --keys "down*2 enter"` | opens an app / sends keys | Physical |
| `python tools/piblo_flash.py --auto [--bin p] [--usb] [--allow-branch] --yes` | stage bin to Core fw dir, `fwcheck` (OTA) or MAZOTA (USB), confirm version | Physical; prints a plan only without `--yes` |
| `python tools/piblo_benchmark.py -n 20` | status / screen / key->change latency (median, p95) + in-process router timing; JSON in `tools/bench/` | Device numbers physical; router numbers in-process (no device) |
| `python tools/visual_diff.py a.png b.png` | pixel diff % + diff PNG | Offline |

Tests: `cd host && python -m pytest tests/test_tools.py`.
Flashing safety: refuses same/older versions (`--force` overrides) and non-origin/main bins unless `--allow-branch`.
