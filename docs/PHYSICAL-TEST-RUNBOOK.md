# NOD physical acceptance runbook — Cardputer ADV

**Status: test plan, not proof of a physical test.** Last reviewed 10 October 2026. This page is deliberately separate from the landing-page marketing copy. It does not assert that any device was tested or flashed.

## Architecture and source of truth

- Public [NOD](https://github.com/manazoid4/nod) repository: product story, launch-page source and founder audit.
- Actual **firmware and Windows MAZ Core**: [MAZ Pocket, deploy/local](https://github.com/manazoid4/maz-pocket/tree/deploy/local). Review this branch and its current release before executing any command.
- Hardware: **M5Stack Cardputer ADV**, paired to MAZ Core running on a Windows PC. Speech/model inference and agent execution primarily run on the PC/cloud, not in the ESP32.
- The 10 Oct private audit is in unified-memory-database (PR #126). It identified an **expired-session approval defect (N-1)** and **offline device status represented as up-to-date (N-2)**. Do not declare agent approvals safe or firmware current based on host UI alone.

## Equipment

- Cardputer ADV and enough battery; switch **ON while charging** (M5Stack instructions).
- Known-good **USB-C data cable**, Windows PC running MAZ Core, phone on the same private Wi-Fi, optional microSD with M5Launcher.
- Access to the existing paired Core authentication **without copying tokens into screenshots, logs, public issues or Git**.
- A camera/phone for physical screen evidence. Record the device version/build and Core commit separately.
- Before any reflash: preserve any device config and confirm a recovery method. Never wipe just to clear a confusing status.

## Session 1: power up and baseline (no update, no agent execution)

- [ ] Switch Cardputer **ON**, connect USB-C power/data and allow it to charge. Confirm keyboard, display and battery/power indication; if no response, check cable/port before entering a bootloader.
- [ ] Photograph **Tools → Device info** if accessible. Write down physical **firmware version, short commit, slot** and actual visible battery indicator; do not infer these from the PC hub.
- [ ] Open existing MAZ Core on Windows. Inspect `http://127.0.0.1:8787/health` and the authenticated control page `http://<PC-LAN-IP>:8787/control/`. A healthy Core does not mean the Cardputer is connected.
- [ ] Put PC, Cardputer and phone on the same private LAN. Open `http://mazpocket.local` if mDNS works, otherwise use the device's shown LAN IP. Pair using the current private setup. Do not expose port 8787 publicly.
- [ ] Confirm the **physical device** appears online and the web device view actually updates. If offline, record exact displayed error, current IP, Core health and last-seen version. Do **not** say the firmware is up to date when the version is unknown.
- [ ] Confirm the current firmware, hub and APK/config are not being conflated: firmware, MAZ Core and NOD Kickstarter site are separate things.

**Stop condition:** If the device cannot be observed on the LAN, diagnose connectivity/host pairing before any feature or update test.

## Session 2: actual useful workflows (non-destructive)

1. **CALL / ASK:** From Home press `T` or open CALL, **hold SPACE to speak, release to submit**, inspect transcript/answer, verify audible reply. Use `P` to replay. Note time to first text and first sound; if microphone permission/routing fails, save the exact error. A browser simulation does not count.
2. **BRAIN DUMP / CAPTURE:** From Home **hold SPACE, speak, release**; verify a dated item appears in MAZ Core's dump inbox and survives a restart. Temporarily take Core offline, try a second dump, verify **NOT SENT** and the manual retry (`O`) after reconnection; no silent loss or duplicate.
3. **CONTROL / DEVICE VIEW:** Open the authenticated phone/web UI, observe actual LCD screen and remote key response; verify device/hub version labels and reconnect after Wi-Fi interruption. No privileged PC commands.
4. **Approvals are DENY-ONLY until N-1 is fixed.** For visibility checks use the repo's *synthetic* `host/hooks/test-approval.ps1` with a harmless labelled fake request. Verify a visible request, **DEL (or N) = deny, ESC/L = later, never deny**, and that the PC falls back to its normal terminal prompt if Core/device is unavailable. **Never press A / allow_all or authorise a real agent action while N-1 remains open.** Never use live credentials, payroll, external messages, destructive commands or privileged actions as test payloads.

Record each result as PASS / FAIL / NOT TESTED, with firmware SHA, Core SHA, photo or short log, and reproduction steps. Label any mocked or browser-only result as **SIMULATED**.

## If an update is needed — only after baseline and recovery are confirmed

**Critical:** MAZ Pocket's distributed firmware is an **app-only M5Launcher image**. **DO NOT flash that `.bin` at address `0x0`**, mass erase the flash, or substitute generic UiFlow2/factory-firmware instructions. Doing so can remove Launcher, partitions or user data.

1. Identify the latest reviewed release and its app-only artifact from the [MAZ Pocket release workflow](https://github.com/manazoid4/maz-pocket). Compare build/commit against **Device info**; do not assume the healthy hub's staged firmware is what the Cardputer runs.
2. Confirm the exact binary checksum and **app slot size** (M5Launcher documents `0x170000 = 1,507,328 bytes` for each NOD OTA slot). On Windows use `Get-FileHash -Algorithm SHA256 <path-to-app.bin>` and compare with that release's manifest. Refuse missing hashes or oversized/unknown-format files.
3. Use the existing, documented **M5Launcher SD / verified staging flow**: choose app-only image → VERIFY + STAGE → open Launcher → install to the app slot → reboot. Keep recovery firmware, verified hash and prior build available. Do not treat the separate onboard self-update pathway as interchangeable without checking the actual device layout.
4. Ensure adequate battery and power before transfer (onboard self-update code's documented default is to refuse below 30% unless charging); avoid interruptions. Photograph new **Device info** and repeat the baseline CALL/CAPTURE/connectivity checks.
5. If the device will not boot, **stop** and use M5Stack's official ADV recovery documentation only after choosing the correct recovery image and understanding its data-loss effect. M5Stack download mode is side power **OFF**, hold **G0**, connect USB-C data cable and release; entering download mode is not permission to write an app-only image to 0x0.

## Work the coding agent must finish before broader approvals

- [ ] Fix stale or cancelled `Buddy.decide` requests so **allow_all** cannot change session permission after expiry; make session grants visible, bounded and revocable. Cover late responses and cancelled/offline requests. Retest against the failing synthetic case from unified-memory PR #126.
- [ ] Fix the **offline != firmware current** status so Unknown/Last seen/Host staged/Device running are distinct. Verify against the unavailable-device fixture.
- [ ] Reconcile the seven open MAZ Pocket PRs by actual diff: `#41/#42/#43` may already be incorporated in the `deploy/local` integration; `#56` assumes a private repo although it is now public; `#69` docked power mode still needs hardware evidence. Avoid duplicate merges and do not merge old `main` history blindly.
- [ ] Add secure approval end-to-end tests with exact request/session binding, deny-on-expiry/disconnect, one-time decision consumption and terminal fallback. No unrestricted automatic action.
- [ ] After fixes and actual device availability: repeat approved safe test tasks, then log **100 approval-loop trials**, classify failures, target ≥95% completed *and zero unapproved execution*. Five outside-user onboarding tests are separate Kickstarter launch criteria.

## Minimal evidence report

| Field | Actual observed value (fill only after test) |
|---|---|
| Date + tester | NOT TESTED |
| Cardputer ADV boots / charge | NOT TESTED |
| On-device firmware version, commit, active slot | NOT TESTED |
| Windows MAZ Core version / commit | NOT TESTED |
| Device online / IP (keep private) | NOT TESTED |
| CALL transcript, speech and latency | NOT TESTED |
| CAPTURE persisted / offline retry | NOT TESTED |
| Authenticated Device view / remote keys | NOT TESTED |
| Synthetic DENY, timeout and terminal fallback | NOT TESTED |
| Safety fixes N-1, N-2 merged and independently tested | NOT TESTED |
| Update artifact SHA-256 / rollback (only if changed) | NOT TESTED |
| Evidence location (private, redacted) | NOT TESTED |

**Release gate:** A working landing page, passing firmware CI or a healthy Windows hub is **not** proof of a Cardputer working end to end. Kickstarter hardware funding remains blocked until the seven go/no-go gates in [the 9 Oct audit](audits/NOD-KICKSTARTER-2026-10-09.md) are met.

**Official hardware references:** https://docs.m5stack.com/en/core/Cardputer-Adv and https://docs.m5stack.com/en/guide/cardputer_adv/restore_factory.
