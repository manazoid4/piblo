# Piblo × Hermes: gadget port (20 Nov 2026)

Piblo becomes a physical interface to the owner's Hermes Agent without flashing Hermes firmware.

```
Piblo hardware --(existing /turn/raw, /buddy/*)--> Piblo Core --(Hermes Gadget v1, ws/wss /gadget)--> Hermes gateway plugin --> Hermes Agent
```

Core plays the gadget. Piblo firmware, identity, local commands and approvals are unchanged.

## Map: existing capability → integration point → change → test

| Existing | Integration point | Change | Test |
|---|---|---|---|
| Push-to-talk `/turn/raw` + Core STT | `answer()` before the model router | If the agent is Hermes, send the text to Hermes (`text`) | `test_push_to_talk_core_stt_then_hermes` |
| Same, Hermes STT | `/turn` and `/turn/raw` | `MAZ_HERMES_STT=hermes`: 16 kHz PCM goes up as `audio.start` + channel-1 frames + `audio.end` | `test_push_to_talk_audio_goes_to_hermes_stt` |
| Piblo TTS (`finish_turn`, speak planner) | unchanged | Piblo speaks Hermes's reply in its own voice | existing TTS suites |
| Local commands (remember, reminders, catch-up, PC) | `answer()` order | Still answered by Piblo first | `test_piblo_local_commands_still_answer_first` |
| Approval queue (`Buddy.request/state/decide`) | `HermesAgent.on_prompt` | Hermes `prompt` → Piblo approval; Allow needs the device's HMAC proof; 60 s fail-closed | `test_approval_*`, `test_deny_*`, `test_expired_*` |
| Alerts (`Buddy.post_alert`) | `on_card`, `on_pairing`, unsolicited turns | Cards, pairing code, Hermes-initiated replies show as Piblo alerts | `test_card_*`, `test_unpaired_*` |
| Core data dir + owner-only key files | `hermes-gadget.json` | 32-byte gadget key, TOFU enrol then HMAC | `test_first_connection_*`, `test_key_*` |
| Settings | `config.py` | `MAZ_HERMES_*`, `MAZ_AGENT` | `test_hermes_off_by_default_changes_nothing` |

New code: `host/mazhost/hermes_gadget.py` (protocol client, own thread and loop) and
`host/mazhost/hermes_routes.py` (agent selection, approvals, cards, routes). Firmware: none.

## Two trust relationships

1. Piblo ↔ Core: the existing pairing and device key. Allow on a Hermes question still needs the
   device's signed decision (`/buddy/decide` proof).
2. Core ↔ Hermes: a separate gadget key in `<data dir>/hermes-gadget.json`; the Hermes owner
   approves the pairing code on the Hermes host. `GADGET_ACCESS_TOKEN` goes in `MAZ_HERMES_TOKEN`.
   `GADGET_ALLOWED_USERS` and every other Hermes rule stay in force on the Hermes side.

## Approval rules (fail closed)

- A Hermes question is a Piblo approval request (`agent: Hermes`, idempotency key `hermes:<id>:<prompt id>`).
- "Allow this command?" is `sensitive` (hold-to-approve where the firmware has it).
- Only a fresh, signed Allow sends `yes`. Deny, expiry (60 s), Hermes withdrawal or a dropped
  connection send `no` or nothing. A decision is never sent on a later connection.
- A question already answered gets no second answer; one Hermes re-sends after a reconnect is answered `no`.
- "Can't show this command" is never answered yes: Piblo shows "approve on PC".
- Halt and locked categories still deny automatically.

## Advertised capabilities

Display 240×135 ASCII (40 cols), mic 16 kHz PCM16, inputs talk/cancel/up/down (hold), cards v1
(6 items, no QR), dashboard v1. No speaker unless `MAZ_HERMES_VOICE=hermes`. No device actions
(an `action` is refused once), no choices, profiles, faces, OTA or interpreter.

## Turn it on (Maz's PC)

1. On the Hermes PC: `hermes plugins list` (plugin present?) and `hermes gadget info` (URL).
   Installing the plugin or changing the gateway needs Maz's OK:
   `hermes plugins install https://github.com/Adolanium/hermes-gadget-sdk/tree/main/plugin --enable`.
2. Core `.env`: `MAZ_HERMES_ENABLED=1`, `MAZ_AGENT=hermes`, `MAZ_HERMES_URL=<url from info>`
   (and `MAZ_HERMES_TOKEN` if the gateway sets one). Restart Core.
3. Piblo shows "Pair Hermes: CODE". On the Hermes PC: `hermes gadget pair`, check the code, approve.
4. `GET /hermes/status` shows `connected` and `paired`. Ask by voice.

Switch brains any time: `POST /hermes/agent {"agent":"piblo"|"hermes"}` (kept in `hermes-agent.json`).
Private mode never routes to Hermes.

## Routes (additive, Core token required)

`GET /hermes/status`, `POST /hermes/agent`, `POST /hermes/ask`, `POST /hermes/cancel`, `POST /hermes/new`.

## Not done / next

- Hermes reply audio is received (`MAZ_HERMES_VOICE=hermes`, tested) but not yet played on the
  device; Piblo speaks the text with its own TTS.
- Device cancel during a Hermes turn: use `POST /hermes/cancel`; firmware does not call it yet.
- Hermes dashboard is exposed in `/hermes/status` but not drawn on the device.
- PR #109 (closed) is app auto-update UI only; nothing Hermes-related to reuse.
