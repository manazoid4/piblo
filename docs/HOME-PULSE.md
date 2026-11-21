# Home Pulse — one household signal, one button

Status: implemented behind an opt-in flag (19 Nov 2026). Synthetic-event tested; no real
Home Assistant instance or parcel service is required or contacted.

A signed webhook becomes a plain Buddy status card with **Acknowledge** and **Later**
(the existing `/buddy/alert` ring, scheduler, expiry and offline queue — nothing new).
It can never approve, decide or run anything: the only effect is an `info` alert.

## Enable (owner, on the PC running Core)

```
MAZ_HOME_PULSE_ENABLED=true
MAZ_HOME_PULSE_SECRET=<long random string, kept private>
MAZ_HOME_PULSE_QUIET=22:00-07:00   # optional; events inside quiet hours wait until it ends
```

Restart Core after changing these. With the flag off (default) the endpoint answers 503.

## Send an event (synthetic Home Assistant door sensor example)

```bash
BODY='{"provider":"home-assistant","event_id":"door-2026-11-19-1","title":"Front door open","body":"sensor.front_door = on","ts":'$(date +%s)'}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$MAZ_HOME_PULSE_SECRET" -hex | awk '{print $NF}')
curl -s -X POST http://localhost:8787/home/pulse/event \
  -H "Content-Type: application/json" -H "X-Pulse-Signature: $SIG" -d "$BODY"
```

Allowed providers: `home-assistant`, `parcel`, `timer`. `event_id` must be unique per
event (replays are deduped); `ts` is epoch seconds and must be within 5 minutes of Core's
clock; optional `expires_in` (seconds) makes the card expire unanswered.

## Security model

- HMAC-SHA256 of the raw body under the owner's secret; constant-time compare. Wrong or
  missing signature → 401, nothing stored.
- Provider allowlist; unknown provider → 403. Stale timestamp → 403.
- Durable `event_id` ledger (pruned to the replay window) → a replayed webhook never
  creates a second card. Owner can clear it: `POST /home-pulse/reset` (bearer token).
- The webhook holds no Core bearer token and reaches no privileged route; injected
  `kind`/`actions` fields are ignored. Cards only ever offer ack/snooze.
- `GET /home-pulse/status` (bearer) reports enabled/configured/providers/quiet — never the secret.

## Limits (honest)

- Proven with synthetic events and the host test suite only; a real Home Assistant
  automation or parcel webhook has not been connected. No broad HA compatibility claim.
- Quiet hours delay events; they do not batch or summarise them.
