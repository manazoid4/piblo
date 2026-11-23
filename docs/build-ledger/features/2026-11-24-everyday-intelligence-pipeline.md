# Feature: Everyday intelligence pipeline (Find My Devices, "sort this out", "carry on")

- **Date:** 2026-11-24
- **Objective:** Turn the capability registry into one fast, interruptible pipeline that completes real everyday tasks: "Piblo, find my AirPods", "Piblo, sort this out", "Piblo, carry on where I left off".
- **Why it matters:** These are repeat-use, time-saving tasks where Piblo's physical presence (handheld + phone + PC) helps. All three common paths complete with **no model call**.

## What was built

### 1. Permission enforcement (prerequisite for autonomous actions) — `host/mazhost/universal_registry.py`
- `Capability.risk` = `read | act | sensitive`, plus `autonomous_ok`.
- `PermissionPolicy`: `required_permissions` are now **enforced** (previously only listed). Grants live in `<control_dir>/permissions.json`; a caller can't grant itself anything through the execute body.
- Automatic origins (`voice`, `router`, `agent`, `assist`, `pipeline`) need a device-signed approval for `act` capabilities unless `autonomous_ok`; `sensitive` capabilities **always** need one, whatever the origin.
- Approvals go through the existing Buddy queue (device HMAC proof). `ApprovalGate` (`assist.py`) makes each approval **single-use** and bound to the exact capability + arguments.
- Turning on a non-default permission (`POST /permissions`) itself needs a device yes, so a process that only has the Core token cannot grant itself `browser_history`. Turning a permission off is always instant.
- Defaults (so nothing existing breaks, minimal setup): `notifications, device_control, find_devices, screen_read, pc_fix`. Not default: `browser_history`.
- Existing capabilities keep their behaviour for user-origin HTTP calls (default risk `act`).

### 2. Assist pipeline — `host/mazhost/assist.py`, `assist_routes.py`, `intents.py`
- `text -> assist.ack (instant) -> route -> permission/approval -> execute -> verify -> assist.say`; every step is an EventBus event (`assist.*`), so PibloCraft / devices can stream it.
- Routing: Tier 0 `intents.py` (find / ring / sort out / resume / yes / cancel) → existing `CommandRouter` (Tier 0 commands + Tier 1 local model). Model offline → a useful reply, not a hang.
- Follow-ups: results can offer `next` ("ring it", "fix it", "carry on"); "yes" runs it, "ring it" uses the last found device.
- Interruptible: `POST /assist/runs/{id}/cancel`, `POST /assist/cancel` (session).
- Measured: every run logs ack/route/exec/total ms, tier, capability, outcome to `<control_dir>/assist-metrics.jsonl`; `GET /assist/metrics` gives success rate, p50/p95, model-free share.
- The device's spoken `/turn` path uses the same pipeline for these intents (provider `piblo-assist`); ordinary chat is untouched (tested: "where is the Eiffel Tower?", "continue", "yes" with nothing pending all still go to chat).

### 3. Find My Devices — `host/mazhost/find_devices.py`, `find_devices_routes.py`, Android `FindDevicesScreen.kt`, `BleScanner.kt`, `proto/FindDevices.kt`
- Device book in `<control_dir>/find-devices.json`; providers: `piblo_phone`, `piblo_handheld`, `apple_findmy`, `google_find_hub`, `bluetooth`.
- Android companion: foreground-only BLE scan (`BLUETOOTH_SCAN` with `neverForLocation`; location permission only on Android 11 and older), posts sightings to `/finddevices/sightings` every 2 s; hotter/colder meter from live RSSI with a wide, honest range and confidence.
- Core: proximity bands, trend (closer/further), last seen, Apple Continuity proximity-pairing decode (model id + approximate battery). Apple accessories rotate addresses, so a nearby advert is reported as "an AirPods Pro-type device", **never** as the user's own; only bonded devices are matched by name/address.
- Actions: ring a Piblo phone (existing `find_phone` alert → loud alarm on silent) or handheld (urgent alert tone); **hand off** to Apple Find My (`icloud.com/find`) or Google Find Hub (app if installed, else web) in the user's own signed-in browser. No Apple/Google credentials are ever requested or stored.
- Optional PC scan `POST /finddevices/scan` when `bleak` is installed (not a dependency; reports `available:false` otherwise).
- Capabilities: `devices.list`, `devices.find` (read), `devices.ring`, `devices.handoff` (act, autonomous_ok).

### 4. "Sort this out" — `host/mazhost/troubleshoot.py`
- Evidence: foreground window title, screen capture + OCR (existing `screen_perception`), probes for DNS, internet (by IP), disk, memory, hung windows (`tasklist`). Probes go through the private-mode guarded client; in private mode nothing reaches out.
- Rule table (no model): app not responding, port in use (finds the owning process), Python/Node missing module, command not found, cert/clock, offline vs DNS vs page down, disk low, memory high, access denied, restart pending.
- Fixes are `sensitive` capabilities (device yes every time): `fix.app.end_task` (refuses Windows system processes and Core itself), `fix.network.flush_dns`, `fix.time.resync`, `fix.disk.clean_temp` (only files >24 h old in the user temp folder).
- Verify: `troubleshoot.verify` re-runs the probe that found the problem and says whether it is fixed.
- Model only when no rule matches and there is screen text (`model_router.chat(..., "auto")`), with graceful failure.

### 5. "Carry on where I left off" — `host/mazhost/resume.py`
- Packet from: unfinished agent tasks (`/buddy/task` store: PibloCraft, hooks, Hermes), `/where` items (park, focus, saved link, last project document), and the last browser pages from Chrome/Edge/Brave history (read from a temp copy, **only** with `browser_history` granted).
- `resume.continue` reopens the document (inside project roots only) and page on the PC and publishes `craft.resume.requested` with the task and its context for PibloCraft/agents.

## New endpoints (additive; no existing contract changed)
`POST /assist`, `GET /assist/runs/{id}`, `POST /assist/runs/{id}/cancel`, `POST /assist/cancel`, `GET /assist/metrics`, `GET|POST /permissions`, `GET /finddevices`, `POST /finddevices/devices`, `GET|DELETE /finddevices/devices/{id}`, `POST /finddevices/devices/{id}/ring`, `POST /finddevices/sightings`, `POST /finddevices/scan`. Not yet written into `docs/protocol/` — needs the orchestrator (AGENTS.md rule).

## Evidence (cloud container, 24 Nov 2026)
- Core: 47 new tests (`test_assist.py`, `test_assist_app.py`, `test_find_devices.py`, `test_troubleshoot.py`, `test_resume.py`) pass; full suite in 4 batches: 1086 passed, 3 failed — `test_authority::test_pc_full_grant…` and `test_work_upgrade::test_real_windows…` fail identically on the untouched baseline (need `pwsh`/Windows); the third (`test_private_mode` guard) was caused by this work and is fixed.
- Android: `./gradlew testDebugUnitTest assembleDebug lintDebug` passes (53 unit tests incl. 5 new), debug APK builds.
- Benchmark `python scripts/benchmark_assist.py --inprocess --rounds 10`: 50/50 tasks completed; pipeline p50: find 0.2 ms, ring 2.0 ms, where-was-I 0.5 ms, sort-this-out 22 ms (p95 46 ms); ack at 0 ms; HTTP wall p50 4–27 ms; all Tier 0 / model-free.
- Real probes on this Linux container: diagnosis in 47 ms, correctly reported healthy.

## Not verified (needs Maz's PC / phone) — see TEST-BATCH.md
- No physical device, phone or Windows PC was available in this session: the Android screen was compiled and unit-tested but not seen on a phone; BLE scanning, ringing via the phone, Find My / Find Hub hand-off, Windows fixes (`tasklist`, `ipconfig`, `w32tm`), OCR on a real screen and browser history paths were not run on real hardware.
- `neverForLocation` may filter some beacon-type adverts on Android 12+; bonded devices are unaffected. Whether Apple proximity adverts survive the filter needs a phone test.

- **Branch / PR:** `ccr-eab81452-54wunq` (public `manazoid4/piblo`).
- **Status:** Built + tested in software; hardware verification pending.
