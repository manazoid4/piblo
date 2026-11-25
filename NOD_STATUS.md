# Piblo status

Updated 7 Nov 2026. 🟢 done with evidence · 🔵 building now · 🟠 needs Maz · ⚪ later · 🟡 merged or built, not yet seen on the device
Every feature: [FEATURE_INVENTORY.md](FEATURE_INVENTORY.md) · Device tests: [test ledger](docs/evidence/TEST-LEDGER-2026-10-11.md) · v1.5 device steps: [V1.5-TEST.md](docs/V1.5-TEST.md)

**Current level: v1.5.** A firmware tick only goes green when the feature is seen working on the real Cardputer, not just in the build or in CI.

## Physical blocker
- [ ] 🟠 **Battery:** old firmware (d88e714) blocks OTA unless charging. Charge the Cardputer fully before attempting U update or flashing v1.5. Do not test at low battery.

## Decided
- 🟢 Brand and web layout: Concept A "Toy-shop warm" (Maz's choice). Blue talk dot inside the warm palette. Not open for re-discussion.
- 🟢 Firmware has a second OTA slot (`nodfw1` / `nodfw2`, 1,507,328 B each). The device's U update works: it installed build d88e714 into `nodfw2`. Every build must stay below 1,507,328 B.

## Level 1: Recover Piblo
- [x] 🟢 Import MAZ Pocket firmware + Core: [PR #14](https://github.com/manazoid4/nod/pull/14)
- [x] 🟢 Find the real device build: v1.0.2 (`nod-v1.0.2-b25`), not v0.8.0 ([ledger #1](docs/evidence/TEST-LEDGER-2026-10-11.md))
- [x] 🟢 Save unpushed GTX PC work to GitHub: `maz-pocket` branches `recovery/mazpc-*`
- [x] 🟢 Feature inventory, nothing retired: [FEATURE_INVENTORY.md](FEATURE_INVENTORY.md)
- [x] 🟢 Bring v1.0.2 into Nod: [PR #16](https://github.com/manazoid4/nod/pull/16) (merged)
- [ ] ⚪ Rename MAZ Pocket → Piblo in screens and Core (keep API compatible)

## Level 2: Bring Piblo to life
- [x] 🟢 Device boots v1.0.2 and talks to Core over Wi-Fi ([ledger #1–2](docs/evidence/TEST-LEDGER-2026-10-11.md))
- [x] 🟢 Brain dump reaches the inbox ([ledger #5](docs/evidence/TEST-LEDGER-2026-10-11.md))
- [ ] 🟡 Voice: local-first voice merged to main ([PR #25](https://github.com/manazoid4/nod/pull/25)). Still to measure on the device: right answer, but 4.4 s and robotic before ([ledger #4](docs/evidence/TEST-LEDGER-2026-10-11.md)). Research: [VOICE-RESEARCH](docs/evidence/VOICE-RESEARCH-2026-11.md)
- [ ] ⚪ Saved notes survive a restart
- [ ] ⚪ One useful PC action end to end, with approval
- [ ] 🟡 Talk circle Home: merged to main ([PR #26](https://github.com/manazoid4/nod/pull/26)). Not yet seen on the device.

## v1.5 (current level, team brief 7 Nov)
Done means running on Maz's real Cardputer and MAZPC, not just merged.
- [ ] 🟡 1. Device: talk circle Home + blue talk dot in the warm palette; charging mode app back; no battery block on update; VERSION 1.5.0 on screen. Battery block removed on main by [PR #20](https://github.com/manazoid4/nod/pull/20) (merged). Device still runs d88e714, which has the block.
- [ ] 🟡 2. Voice: a real turn measured before and after, faster and less robotic. Local first, cloud failback ready for a key. 19 Nov: streaming STT drops the speech-to-text wait after end of speech from 456 ms to 67 ms on synthetic clips (`MAZ_LIVE_STREAM_STT=1`, off by default, [evidence](docs/evidence/FLOW-STREAMING-AND-BRIDGE-2026-11-19.md)). Not measured on the device.
- [ ] 🟡 3. Saved notes survive a restart (proved on the real device). PC half proven 19 Nov: real Core process killed and restarted, note recalled (`host/tests/test_core_restart_proof.py`, [evidence](docs/evidence/FLOW-STREAMING-AND-BRIDGE-2026-11-19.md)). Device Notes app not yet seen.
- [ ] 🟡 4. One useful PC action end to end, with approval on the device (reuses Control Center and the allow-listed PC actions). Core half proven 19 Nov: device approval gates a job sent to PibloCraft, deny never sends (`host/tests/test_piblocraft_bridge.py`, approval simulated via the API, [evidence](docs/evidence/FLOW-STREAMING-AND-BRIDGE-2026-11-19.md)). Not yet pressed on the real device.
- [ ] 🟡 5. Core serves the right build, so pressing U installs main, never an old one. (Core staged an old build once; not yet fixed in a verified run.) 19 Nov read-only check: Core serves 1.5.4 / fba9107 and the served bin matches its sha256, but 4 newer firmware commits on main are not staged, so still yellow ([evidence](docs/evidence/FLOW-STREAMING-AND-BRIDGE-2026-11-19.md)).

Steps for Maz to prove each item: [docs/V1.5-TEST.md](docs/V1.5-TEST.md).

## Level 3: Make it beautiful
- [ ] 🔵 Piblo Core: real Windows app (Electron, tray, onboarding). Draft [PR #19](https://github.com/manazoid4/nod/pull/19), not needed for v1.5.
- [x] 🟢 Website: Concept A chosen by Maz as the layout.
- [ ] ⚪ One design language across site, app and device

## Level 4: Show the world
- [ ] ⚪ Real device clips (film plan in PR #15)
- [ ] ⚪ Teaser + demo video
- [ ] ⚪ Exploded hardware + case visuals

## Level 5: Kickstarter ready
- [ ] ⚪ Open-source release with downloads anyone can follow
- [ ] ⚪ Community: feature voting, case gallery
- [ ] ⚪ Campaign page, costs, rewards
