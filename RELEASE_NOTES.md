v1.5.6 (Piblo board link no longer sticks half-linked; link state in Core status). v1.5.5 (Piblo mic can dictate into a Craft draft on the PC). v1.5.4 (board link retries ESP-NOW start; System screen shows link state). v1.5.3 (optional ESP-NOW offload to the Piblo board; HTTP fallback unchanged). v1.5.2 (approval details render on the device for both Core heads; Flow/Type-on-PC upload no longer freezes the screen). v1.5.1 (Piblo name everywhere on the device; old MAZ Pocket name removed). v1.5.0 (nod v1.5: blue talk dot in the Toy-shop warm palette, charging mode, no OTA battery gate). See CHANGELOG.md for details.

# MAZ Pocket v1.0.0 — WORK CONSISTENCY

v1.0.0 adds a durable WORK consistency layer to the proven CONTROL foundation without turning the Cardputer into an app drawer.

Home remains exactly six surfaces, with the stable internal `flow` identifier preserved:

**CALL / CAPTURE / AGENTS / CONTROL / MEMORY / WORK**

## WORK on phone and Cardputer

- The authenticated phone centre is now `WORK | AUTHORITY`, with WORK open by default and AUTHORITY preserved.
- Application is a literal one-tap action. A 600 ms client debounce improves feel; unique event IDs and transactional server idempotency are the real retry/double-count protection.
- Undo Last reverses only the current authenticated session's latest event, never another phone/device's work.
- Today/current cards show targets and raw event counts. MAZ WORKS never invents close-rate or productivity metrics.
- Manage Tracks creates/edits/reorders/pins/pauses/archives custom count, time or check-in tracks. Event-type IDs stay stable across renames, so history remains attributable.
- Daily and weekly targets use host-local calendar boundaries, including DST transitions. Seven-day history is bounded.
- Cardputer WORK opens directly to the glance dashboard for JOB HUNT, MAZ WORKS and pinned custom tracks. Focus, Sprint, Tasks, Reminders, Shift and Retro remain under WORK TOOLS.
- Existing Host polling is reused: 10 seconds normally, 20 seconds in FIELD. At roughly three missed polls, last-known values remain visible and are labelled STALE.
- Firmware accepts at most four pinned tracks, exactly seven daily history entries and no response above 4096 bytes. Malformed/oversized/offline data never becomes a fake zero.

## Storage and upgrade safety

- WORK uses a versioned SQLite database under the existing MAZ Core data root, with WAL, foreign keys, ordered transactional migrations and idempotent template seeding that never overwrites user edits.
- Event writes and reversals use immediate transactions; fresh/repeated/concurrent bootstrap, retry, interruption, corruption and timezone cases have dedicated tests.
- The Windows updater integration test proves a v0.8 installation keeps `.env`, authority/pairing state and WORK events through upgrade.
- Release packaging remains app-only for M5Launcher and preserves non-destructive launcher hand-back.

## Preserved CONTROL foundation

## CALL MAZ

- The old internal `COMM` experience is now explicitly **CALL MAZ**.
- Hold Space to speak; release to send.
- Spoken MAZ reply playback is enabled on fresh installs.
- **P** replays the last reply.
- **V** toggles voice replies.
- **A** changes LOCAL / AUTO / CLOUD / MAZLATEST directly on the Cardputer.
- **MAZLATEST** routes through authenticated MAZ Core to PC-local 9router model `MazLatest`; it fails explicitly rather than misreporting fallback provenance.
- Fresh v1 installs prefer CLOUD; existing devices keep their persisted route.
- Context Ask remains available and informational only.

## CAPTURE + Teach-by-Demonstration

CAPTURE now opens a clear choice:

- **TEACH DEMO** — selected PC display recording + microphone narration + MARK events.
- **BRAIN DUMP** — voice to structured notes.
- **VOICE RECORDER** — save audio directly.

Teach Demo is host-side and explicit:

1. choose a Windows display from the Cardputer;
2. start recording;
3. press **M** for important moments;
4. stop from the Cardputer;
5. MAZ Core extracts sparse scene-change frames and transcribes narration;
6. save the result to Results Inbox.

The default compatibility target is approximately **3 GB VRAM**. Recording is CPU-oriented and ECO/BALANCED faster-whisper profiles use CPU int8. ffmpeg is the capture dependency. MAZ Core auto-selects the first DirectShow microphone unless `MAZ_TEACH_AUDIO_DEVICE` is configured.

## Agent workbench

**AGENTS** now contains:

- **AGENT STATUS** — Agent Nudge evidence.
- **PLAN** — current-project-aware implementation planning without execution.
- **CREW** — decomposes work across supported installed Claude/Codex/Hermes CLIs.
- **RETRO** — proposes Template / Skill / Knowledge / Guard improvements after work.
- **PROJECTS** — Git/build/test/project evidence.

CREW planning is read-only. Actual Claude/Codex/Hermes subprocess execution requires a signed phone-approved **PROJECT FULL or broader** grant. The first implementation executes packages serially on one checkout to avoid blind file collisions.

## Prompt Deck

**MEMORY → PROMPT DECK** provides project-aware templates for:

- bugs and diagnosis;
- UI and feature work;
- open-source integration;
- refactoring and testing;
- security/repository audits;
- research + build;
- MAZ Pocket capabilities;
- agent workflow improvement.

Templates keep a consistent task/context/procedure/verification/deliverable layout while the compiler injects current project + Agent Nudge evidence.

## Phone-approved full PC authority

MAZ Core now has an external authority broker.

The model can request control but cannot approve itself. The authenticated phone page under `/control/` can approve or deny short-lived grants for:

- READ ONLY;
- PROJECT FULL;
- PC FULL;
- ADMIN.

The phone UI also shows active grants, audit events and **REVOKE ALL CONTROL**.

Signed grants are checked before arbitrary PowerShell/cmd execution and before coding-agent subprocess launches. `PC FULL` removes project confinement. `ADMIN` is a separate authorization scope; actual Windows administrator capability still depends on the Windows process/helper privileges.

## Pairing + Token ID

**CONTROL → PAIRING + PHONE** now shows:

- a safe 12-character Token ID fingerprint;
- the saved pairing token hidden by default;
- **P** to reveal/hide the real token on the physical Cardputer;
- the phone-control URL.

The unauthenticated `mazpocket.local` page exposes Token ID only, not the raw secret.

## `mazpocket.local` rebuilt

The local Cardputer page is now designed as a phone-first control centre rather than a diagnostic dump.

It surfaces:

- pairing + Token ID;
- phone approvals;
- CALL MAZ;
- PLAN / CREW / RETRO / Prompt Deck;
- cloud/local/AUTO route;
- voice playback setting;
- Core/agent/laptop status;
- PC quick controls;
- live LCD;
- connection configuration;
- firmware staging.

## M5Launcher self-deletion bug fixed

Previous MAZ builds returned to M5Launcher by intentionally overwriting the first four bytes of the running MAZ app. That made the MAZ slot unbootable and caused the observed **“open Launcher → MAZ disappears → reinstall from SD”** loop.

v0.8 no longer invalidates the running firmware. It selects the valid Launcher/fallback app as the next boot partition with `esp_ota_set_boot_partition()` and restarts.

CI contains a dedicated regression guard that fails if destructive running-app flash writes return to this path.

## Debug Capsules

MAZ Core can persist a bounded, redacted debugging snapshot containing project/Git state, Core/device/system/model status, recent jobs, Agent Nudge and authority state. Credential-shaped values are removed before persistence/model use.

## Installer changes

`START-HERE.cmd` is explicitly opt-in:

- asks before MAZ Core install/update;
- asks before copying Cardputer firmware to SD;
- refuses to guess if multiple removable drives are attached;
- asks before opening the browser;
- does not format media or directly write Cardputer flash.

## Physical validation gate

CI proves host tests, browser behavior, packaging and firmware compilation, but not physical Cardputer/M5Launcher behavior. On the actual Cardputer ADV:

1. Confirm Home is CALL / CAPTURE / AGENTS / CONTROL / MEMORY / WORK.
2. Open WORK and confirm the JOB HUNT + MAZ WORKS glance appears directly.
3. Log Application and one MAZ WORKS event from the phone; confirm the next-poll update.
4. Undo Last; confirm the reversal on Cardputer.
5. Disconnect Core; confirm last-known values remain and show STALE, never blank/zero.
6. Confirm WORK TOOLS reaches Focus/Sprint/Tasks/Reminders/Shift/Retro.
7. Recheck CALL, CAPTURE → Teach Demo, AGENTS → Plan/Crew/Retro, authority approve/deny/revoke, pairing token reveal, `mazpocket.local` OPEN WORK and Ctrl+L M5Launcher return.
8. Upgrade from a fresh v0.8 install using the v1.0.0 M5Launcher artifact; confirm `.env`, pairing and WORK data persist.

The source/build gates are complete only when accompanied by this physical evidence; no automated result substitutes for it.
