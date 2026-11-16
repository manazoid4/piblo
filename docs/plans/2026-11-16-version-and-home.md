# Plan: see the version at a glance + tidy Home (16 Nov 2026)

Owner ask: "a better, clearer way to see the version, put it on the home screen, organise the whole thing better". Execute after 4 pm.

## Why (evidence, 16 Nov)
- Maz believed the Cardputer was on the latest firmware. It was on **1.5.1**: boot line `Piblo 1.5.1 READY`, and port 8022 greets with `Piblo 1.5.1`. Core had 1.5.2 staged, but the device never requested `/fw`.
- Today the version only shows on the boot splash (`src/core/shell.cpp:283`), Keys & Help header and the System screen. Home shows none of it.

## Done means
1. Home status bar always shows `v1.5.x` (small, dim, right side). Readable in a Live View screenshot.
2. When Core has a newer staged build, Home shows `Update 1.5.2 ready - press U`. After installing, the bar shows the new version. If the update fails, it shows one plain reason (from `fwFail`).
3. Settings/System has one **Version** row: `Piblo 1.5.2 (a579100) · Core 1.5.1 · Up to date`.
4. Piblo app Home shows the same line. Core remembers the version the device last reported and returns it in `/core/cardputer/status`.
5. `scripts/piblo_remote.py status` prints the device version with no token, using the port 8022 greeting.

## Steps (one small PR each, firmware last)
1. **Script** (no firmware): `piblo_remote.py version` reads the 8022 greeting. Test against the live device.
2. **Core**: store `fw_version` from `/health?fw_version=` and the live `hello`. Return it plus the staged version in cardputer status. Add a host test.
3. **App**: the version line on Home. `npm test` plus a screenshot.
4. **Firmware**: draw the version in `ui::statusBar` and add the "Update ready" chip, reusing the `fwIsNewer` result from the precheck. Check flash headroom against the CI gate (it was only 48 B over at the last check). Flashing needs Maz's yes.
5. **Tidy Home (needs Maz first)**: list every screen and key in one table, propose a one-page layout (what stays on the 4 cards, what moves to More), and get Maz's OK before moving anything.

## Blockers to clear first
- Laptop Core `.env` has no `MAZ_TOKEN`, so remote screenshots fail on MAZ-LAPTOP. Setting one is a credential change: ask Maz.
- The Cardputer is still on 1.5.1. Press U (approved in the 16 Nov handoff), then confirm with the 8022 greeting.
