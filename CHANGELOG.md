# Changelog

## v1.5.6

- Cardputer keeps sending HELLO until the Piblo board answers, so a missed HELLO no longer leaves the link stuck (linked but every PING dropped).
- Core status reports board_linked, board_healthy, board_diag, board_mac.

## v1.5.5

- Craft voice: an explicit Piblo mic capture can be routed to a Craft draft on the PC (Windows Craft pilot); empty captures report "No speech detected".

## v1.5.4
- Board link: ESP-NOW starts again if Wi-Fi was off at boot or switched off later.
- System screen shows the board link state (radio, key, HELLOs sent).
- Updates arrive at once: when new firmware is staged on Core, or Piblo first connects, Core tells Piblo to check straight away. "Update device" on the web page now refreshes the check first, then asks you to confirm.
- Piblo checks for a new version about 2 s after Wi-Fi connects, and always asks your PC on the home network first (the remote link is only the fallback).
- New setting "Auto-update on charger" (off by default): when on, Piblo installs a staged update by itself while charging and idle for 2 minutes on Home. A bad update still rolls back.

## v1.5.3 — ESP-NOW offload to the Piblo board (16 Nov 2026)

- When a Piblo board is paired and healthy, Flow / transcribe uploads go over ESP-NOW and the board forwards them to Core; otherwise (or on any failure) the direct HTTP upload runs exactly as in 1.5.2. System screen shows Board status.

## v1.5.2 — approval details on device + Flow upload that never freezes (16 Nov 2026)

- Approval cards show their detail lines (amount / recipient / category) again: the firmware accepts both the current `ASK ON PIBLO` head and the legacy `THE NOD` head, so current Core no longer drops the amount and recipient (#152).
- Flow / Type-on-PC upload moved off the main loop onto its own task with an `UploadGate` state machine: no double-send, ESC cancel, 25 s HTTP / 28 s UI timeout with a clear error, and the screen, keys and approvals keep responding while sending (#156).
- Type-on-PC header no longer sits under the error text; while sending the Buddy pulses with "Sending..." and an ESC countdown (#156).

## v1.5.1 — Piblo name everywhere (8 Nov 2026)

- Device shows Piblo, never MAZ Pocket: home title, Settings, Wi-Fi hotspot (Piblo-Setup, password piblosetup), web address piblo.local, serial banner.
- Core prompts, installers and release file names say Piblo.
- Kept so nothing is lost: saved settings key, Core data folders (~/.maz-pocket), API paths.

## v1.5.0 — nod v1.5 (7 Nov 2026)

- Home: blue talk dot inside the Toy-shop warm palette (theme.h); talk circle kept.
- Update from Core no longer blocked by battery level (#20).

## Earlier (Nod)

- Nod repo now carries MAZ Pocket **v1.0.2** (tag nod-v1.0.2-b25, the build on the physical device), replacing the v0.8.0 import.
- Added Charging mode and display profiles (standard 240x135, large 480x320 template). See docs/NOD-DEVICE-AND-CORE.md.

## v1.0.2 — nod (build on the device, 11 Oct 2026)

- nod Home, One Lamp look, hold SPACE = brain dump, T = talk; Needs-you queue; Library search.
- Today screen, Quiet mode (approval chime never muted), Fn+H Pause Asks (fail closed).
- Security: blanket approvals disabled, expired requests rejected, agent isolation fails closed.
- Voice: short spoken replies, optional local Kokoro, 16 kHz mono WAV.
- Device tab in Core: live screen, remote keys, one-click update.

## v1.0.0 — WORK CONSISTENCY

- Home remains exactly six surfaces and now reads CALL / CAPTURE / AGENTS / CONTROL / MEMORY / WORK; the stable internal `flow` ID is unchanged.
- WORK opens directly to a Cardputer glance for JOB HUNT, MAZ WORKS and pinned custom tracks, with current/target values, a fixed seven-day strip, and last-known data labelled STALE after three missed Host polls.
- Existing Focus, Sprint, Tasks, Reminders, Shift and Retro remain available one level deeper under WORK TOOLS.
- The authenticated phone control centre is now `WORK | AUTHORITY`, with WORK as the default tab, one-tap Application logging, raw event breakdowns, Undo Last, seven-day history and Manage Tracks.
- Added a versioned WAL/foreign-key SQLite store with idempotent template seeding, stable event IDs, retry-safe writes, session-scoped reversals, archive-safe history, daily/weekly local-time aggregation and crash/interruption coverage.
- Added bounded Cardputer JSON and streaming firmware parsing: at most four pinned tracks, exactly seven history entries, 4096-byte ceiling, and no fake zero replacement on malformed/offline data.
- Added custom track creation/edit/reorder/pin/pause/archive, stable event-type renames, bounded input validation, mobile 360–430 px coverage and server-source-of-truth refresh.
- Added v0.8-to-v1 upgrade verification proving `.env`, authority/pairing state and WORK data survive the shipped installer/update path.
- Release packaging excludes machine-local runtime logs in addition to tests, caches, bytecode and `.env` state.
- Added an explicit fourth MAZLATEST route from Cardputer to MAZ Core, using loopback-only 9router model `MazLatest` with no silent fallback or device-side credentials.
- Preserved CALL, CAPTURE, AGENTS, CONTROL/AUTHORITY, MEMORY, pairing, Beam, screen mirror, firmware staging, M5Launcher hand-back, AI routing and installer behavior.

## v0.8.0 — CONTROL

- CALL MAZ: renamed COMM experience, spoken reply playback on fresh installs, P replay, V voice toggle, A cloud/local/auto route.
- CAPTURE + Teach-by-Demonstration: TEACH DEMO / BRAIN DUMP / VOICE RECORDER; host-side display recording with microphone narration and MARK events, extracted to Results Inbox.
- Agent workbench (AGENTS): AGENT STATUS, PLAN, CREW, RETRO, PROJECTS; CREW planning is read-only, actual subprocess execution requires a signed phone-approved PROJECT FULL+ grant.
- Prompt Deck (MEMORY): project-aware task templates compiled with current project + Agent Nudge evidence.
- Phone-approved full PC authority: external authority broker with READ ONLY / PROJECT FULL / PC FULL / ADMIN grants, approved/denied from the authenticated phone `/control/` page; signed grants gate PowerShell/cmd execution and coding-agent subprocess launches.
- Pairing + Token ID: safe 12-character fingerprint, hidden pairing token with on-device reveal, phone-control URL.
- `mazpocket.local` rebuilt as a phone-first control centre.
- Fixed the M5Launcher self-deletion bug — MAZ no longer invalidates its own running firmware when returning to Launcher; boot partition is switched via `esp_ota_set_boot_partition()` instead, with a CI regression guard against destructive running-app flash writes.
- Debug Capsules: bounded, redacted project/Git/Core/device/system/model/job/authority snapshot for troubleshooting.
- `START-HERE.cmd` installer changes are now explicitly opt-in at every destructive step (MAZ Core install/update, Cardputer SD copy, browser launch); never formats media or writes Cardputer flash directly.

## v0.7.1 — installer hotfix, speed pass and packaging cleanup

- Fixed the Windows install path. `START-HERE.cmd` runs `powershell.exe`, and Windows PowerShell 5.1 decodes a BOM-less script with the ANSI code page. Two em dashes in `setup-all.ps1` therefore arrived as cp1252 text ending in `0x94`, a smart closing quote that PowerShell accepts as a string delimiter, which unbalanced the quoting and produced misleading `}` / `elseif` parse errors at lines 85-92.
- Executed installer scripts are now plain ASCII and are packaged with a UTF-8 BOM, so neither PowerShell edition can mis-decode them.
- `START-HERE.cmd` prefers `pwsh.exe` when present and still falls back to Windows PowerShell 5.1.
- CI now builds the release package, extracts the generated `MAZ-Pocket-v<version>-Install.zip` and validates the scripts users actually download by parsing and dry-running them under real `powershell.exe`, on both the 5.1 fallback and the preferred shell.
- Speed pass: Pocket boot animation cut from 900 ms to 250 ms, the stale v0.5 system prompt replaced with a compact v0.7 one, retained history halved from 24 to 12 turns, SMART/FAST Ollama keep-alive raised to 15/60 minutes, and handheld local generation capped at 160 tokens.
- Packaging cleanup: `MAZ-Core-vX.Y.zip` now ships runtime files only — `tests/`, `.pytest_cache`, `__pycache__` and `*.pyc` are excluded, and that is a permanent release rule.
- No firmware behaviour, product surface or MAZ Core capability changed.

## v0.7 — FIELD (release candidate)

- Home NOW priority strip and programmable quick actions 1–4.
- Global Fn+Space Context Ask with bounded current-screen context.
- Durable auto-retrying Outbox for voice, Context Ask and Beam.
- Beam short text/URL transfer with durable Core queue and Windows clipboard handoff.
- On-demand Laptop CPU/RAM/GPU/VRAM/battery/Ollama status.
- FIELD mode and Shift Clock.
- Smarter local-AI resource profiles: SMART/SAVE/FAST with adaptive context and measured Ollama usage.
- Unified MAZ Core version reporting.
- Full split client + Cardputer ZIPs are now a CI-enforced release rule.
- Documentation and Knowledge Vault architecture updated.

## v0.6

- One-entry Windows install/update flow.
- Stable per-user MAZ Core installation and Core-only USB pairing.
- Phone-first verified firmware staging to SD for M5Launcher.
- Local primary -> backup model failover.
- Hardened nonblocking local portal and authenticated LCD mirror.
- Single version source and release packaging cleanup.

## v0.5.1

- Runtime health instrumentation and bounded Host worker.
- COMM durable WAV path and safer PC actions.
- Verified installer/release workflow hardening.
