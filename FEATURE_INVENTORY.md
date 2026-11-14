# Piblo feature inventory

Source of truth for "nothing useful gets silently removed". Base: MAZ Pocket **v1.0.2** (tag `nod-v1.0.2-b25`, the build on the physical Cardputer), recovered into Piblo in [PR #16](https://github.com/manazoid4/nod/pull/16).

**Columns:** Code = present in this repo · Build = firmware/Core CI green · Tests = unit tests exist and pass · Device = physically verified (link to [test ledger](docs/evidence/TEST-LEDGER-2026-10-11.md)).
✅ yes · 🟡 partial · ❌ failed · ⬜ not yet checked

**Status:** Preserved (kept as-is) · Restored (was missing from Piblo, brought back) · Improved · Renamed · Moved (now under More/Advanced) · Experimental (branch only, unproven) · Retired (with reason)

## Device (Cardputer firmware)

| Feature | Where | Status | Code | Build | Tests | Device |
|---|---|---|---|---|---|---|
| ASK / CALL (push-to-talk voice) | `talk` / `src/apps/comm.cpp` | Restored | ✅ | ⬜ | ✅ | 🟡 answer correct, voice slow + robotic (ledger #4) |
| Voice reply (TTS on device) | `voice`, host `tts.py` | Restored | ✅ | ⬜ | ✅ | ❌ quality/latency (ledger #4) |
| Dictation | `dictate` in shell | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Capture hub | `capturehub` | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Brain Dump (hold SPACE) | `braindump` | Restored | ✅ | ⬜ | ✅ | ✅ note reached inbox (ledger #5) |
| Memory / Results inbox / Notes | `recall`, `inbox`, `notes` | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Today | `today` | Restored (was missing in v0.8.0 import) | ✅ | ⬜ | ✅ | ⬜ |
| Focus timer, Sprint, Tasks, Reminders, Shift | `flowtools` under WORK; Windows Actions | Improved: Windows keeps Finish/Stop after expiry | ✅ | ⬜ | ✅ | ⬜ owner test pending; [13 Nov evidence](docs/evidence/EVERYDAY-POLISH-2026-11-13.md) |
| Alerts / agent events | `notify`, `nudge` | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Alerts + Piblo Buddy (reminder / webhook / ack-snooze, priority sound table) | `alerts.cpp`, `buddy_face.cpp`, `alert_policy.h`, host `buddy.py` `/buddy/alert` | New (this PR; see docs/ALERTS.md) | ✅ | ⬜ | ✅ host + policy table | ⬜ |
| Quiet (approval chime never muted) | `quiet.cpp`, Fn+Q | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Halt / Pause Asks (Fn+H, fail closed) | `approvals.cpp` | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Agent approval | `approvals.cpp`, host `authority.py` | Restored (security P0 fixes) | ✅ | ⬜ | ✅ | ⬜ |
| PC control / Projects & Builds | `core`, `desk`, `control` | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Quick actions (keys 1–4) | Home | Preserved | ✅ | ⬜ | ✅ | ⬜ |
| Send to hub (Beam) | `beam` | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Hub status (CPU/GPU/battery) | `laptop` | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Teach demo (screen record a workflow) | `teach` | Moved (Advanced) | ✅ | ⬜ | ✅ | ⬜ |
| Agents: Plan, Crew, Retro, Prompt Deck | `agents` hub | Moved (Advanced) | ✅ | ⬜ | ✅ | ⬜ |
| Wi-Fi + Pairing & Phone | `network`, `pairing` | Restored | ✅ | ⬜ | ✅ | ✅ link to Core over Wi-Fi (ledger #2) |
| Charging screen | `charge`, `charge_monitor.h` | Improved (Nod-only, from #14) | ✅ | ⬜ | ✅ | ⬜ |
| Display profiles (240x135, 480x320 template) | `firmware/nod/include` | Experimental (large screen unbought) | ✅ | ⬜ | ✅ | ⬜ |
| Utilities: Calc, QR, Stopwatch, Viewer, Generator, Snippets, Recorder, Say, Decision | registry | Moved (More) | ✅ | ⬜ | ✅ | ⬜ |
| Settings, Keys & Help, Device Tests | registry | Preserved | ✅ | ⬜ | ✅ | ⬜ |
| M5Launcher app image + A/B slots | `partitions.csv`, `docs/pocket/OTA-LAYOUT.md` | Preserved | ✅ | ⬜ | — | ✅ v1.0.2 installed, slot valid (ledger #1) |
| Firmware update from Core ("Update firmware?") | shell + host `fw.py` | Restored | ✅ | ⬜ | ✅ | ⬜ |
| Legacy Connections | `wifi` | Moved (Advanced, kept for old setups) | ✅ | ⬜ | — | ⬜ |
| New Home: big talk circle | — | Planned (owner pick 7 Nov) | ⬜ | ⬜ | ⬜ | ⬜ |

## Piblo Core (Windows host, `host/`)

| Feature | Where | Status | Code | Build | Tests | Device |
|---|---|---|---|---|---|---|
| Device link, status, live screen, remote keys | `device.py`, `/core/cardputer/*` | Restored | ✅ | — | ✅ | ✅ screen mirror (ledger #3) |
| Voice turn: STT → LLM → TTS (Fish, optional Kokoro) | `stt.py`, `llm.py`, `tts.py`, `voices.py` | Restored | ✅ | — | ✅ | 🟡 (ledger #4) |
| Models / providers (incl. MazLatest route) | `llm.py`, `config.py` | Preserved | ✅ | — | ✅ | ⬜ |
| Memory: dumps, inbox, Library FTS search, ingest | `dumps.py`, `library.py` | Restored | ✅ | — | ✅ | ✅ dump saved (ledger #5) |
| Needs-you queue | `needs.py` | Restored | ✅ | — | ✅ | ⬜ |
| Approvals / authority (fail closed) | `authority.py`, `security.py` | Restored | ✅ | — | ✅ | ⬜ |
| Executor / jobs / projects | `executor.py`, `jobs.py` | Preserved | ✅ | — | ✅ | ⬜ |
| WORK tracks (job hunt, Maz Works) | `work_*.py` | Preserved | ✅ | — | ✅ | ⬜ |
| Pairing + phone control | `pairing.py`, `phone_control.py` | Preserved | ✅ | — | ✅ | ⬜ |
| Self-update + staged firmware | `selfupdate.py`, `fw.py` | Preserved | ✅ | — | ✅ | ⬜ |
| Web UI `/ui` (Now, Inbox, Approvals, Device, Settings) | `webui.py`, `static/` | Preserved (desktop app being built over it) | ✅ | — | ✅ | ✅ (ledger #3) |
| Desktop app (Electron, tray, onboarding) | `app/` | Implemented; see individual feature evidence | ✅ | ⬜ | ✅ | ⬜ |
| Read complete saved thoughts | Windows Memory → Read; existing `/dumps/{id}` | Improved: original transcript replaces summary | ✅ | local Windows package ✅ | ✅ Electron/IPC + real Core | ⬜ owner unconfirmed; first test failed, subsequent agent Windows test passed ([evidence](docs/evidence/EVERYDAY-POLISH-2026-11-13.md)) |

## Other

## 13 November experimental integrations

| Feature | Implementation | Automated evidence | Physical / remote evidence |
|---|---|---|---|
| Hey Piblo | Local streaming keyword detector gates an already-open Live session | Gate/re-arm tests; 2/2 synthetic wake phrases and 0/20 synthetic negatives at tested gains; default speech gain bypassed for detector to avoid clipping | Real device mic and false-activation rate unconfirmed; not always-on from Home; model-weight rights unresolved |
| Voice shortcuts | Fixed configured webhook or signed-authority PC command; exact request approval before run | Signed device-proof tests; real PowerShell print command under a test-issued grant; no execution on deny | Real owner/device confirmation unconfirmed; direct local/private speech-to-speech has no command-intent hook |
| Fetch from Home | Opt-in folder, bounded originals, handle-verified root; travelling Core can proxy home Core over Tailscale | Auth, traversal, replacement race, remote transport fixture, exact-byte Electron download and text preview | Real two-PC/Tailscale test unconfirmed; no passport/user files used |
| Show me once | Finished PC capture to explicit draft review and saved skill | Backend route/auth/persistence tests; Electron draft/discard/explicit-save flow | Real recording unconfirmed; model sees narration/marks, not frames; no automatic replay |

All four remain unconfirmed on hardware. No firmware was flashed or feature release published.

| Feature | Status | Notes |
|---|---|---|
| Web flasher | Preserved | `web-flasher/`, tests pass in CI |
| Website (`site/`) | Improving | Two concepts being built for owner pick |
| Claude Buddy draft (`firmware/draft/`) | Experimental | Unchanged |

## Experimental branches (kept, not merged)
In `manazoid4/maz-pocket`, all on top of v1.0.2:
- `agents/nod-voice` (#95): streamed sentences, local Kokoro. The voice research picks this direction ([report](docs/evidence/VOICE-RESEARCH-2026-11.md)).
- `feature/nod-audio-profiles-20261011` (#94): speaker tuning, A/B profiles.
- `feature/nod-fish-voice-favourites-20261011` (#93): Fish voice favourites.
- `integrate/nod-core-20261012` (#96) + `recovery/mazpc-*`: GTX PC work saved 7 Nov.
- Open older PRs: #69 docked mode, #56 private-repo self-update, #43 Flow dictation, #42 focus sprint, #41 agent approvals hook.

## Retired
None. Nothing has been removed.
