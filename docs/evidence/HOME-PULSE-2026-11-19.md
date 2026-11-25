# Home Pulse — signed household events → Buddy alerts — 20 November 2026

Feature-build prompt: `piblo-private/prompts/feature-build/04-HOME-PULSE-AND-ROUTINES.md`.
ONE low-risk integration path: a signed webhook carrying a synthetic Home Assistant sensor
state, an opt-in parcel event or a local timer event. Reuses the existing `/buddy/alert` ring,
lazy scheduler, expiry, snooze/ack and offline queue — **no new scheduler, store or app**.

## What changed

- `host/mazhost/home_pulse.py` (new): mounted sub-app `POST /home/pulse/event` (HMAC-SHA256 of
  the raw body, constant-time compare; provider allowlist `home-assistant|parcel|timer`;
  ±5 min timestamp window; durable sqlite `event_id` ledger pruned to the window; quiet hours
  delay the card via the existing `delay_seconds`; `expires_in` passthrough). Owner-only bearer
  routes `GET /home-pulse/status`, `POST /home-pulse/reset`. The webhook can only ever post an
  `info` alert with ack/snooze — injected `kind`/`actions` fields are ignored.
- `config.py`: `MAZ_HOME_PULSE_ENABLED` (default false), `MAZ_HOME_PULSE_SECRET` (never logged
  or echoed), `MAZ_HOME_PULSE_QUIET` (default 22:00-07:00).
- `app.py`: mount + owner routes (3 lines).
- `docs/HOME-PULSE.md`: enable steps + synthetic curl example + security model + honest limits.
- `tests/test_home_pulse.py` (new): 12 tests.

## Evidence

| # | Command | Output | Result |
|---|---|---|---|
| 1 | `python -m pytest tests/test_home_pulse.py -q` | 12 passed (exit 0) | PASS |
| 2 | `python -m pytest tests/test_buddy_alerts.py tests/test_buddy.py tests/test_app.py tests/test_config.py -q` | 70 passed (exit 0) | PASS |
| 3 | `python -m pytest tests -q` (host, after change, `MAZ_OLLAMA_URL=http://127.0.0.1:9`) | 1018 passed, 3 skipped, 0 failed (exit 0) | PASS |

## Prompt test list → coverage

- simultaneous updates → distinct event_ids both delivered (`test_stale_event_rejected_out_of_order_events_delivered`)
- offline queue / reconnect → quiet-delayed alert is `scheduled`, fires on first poll after due (existing lazy scheduler; `test_quiet_hours_delay_instead_of_drop` + buddy's `test_delayed_reminder_fires_when_due`)
- out-of-order events → older-ts event inside the window still delivered, dedupe by id not order (same test)
- duplicate webhook → `test_replayed_event_is_deduped` (second post → `{"deduped": true}`, one card)
- forged signature → `test_forged_or_missing_signature_is_rejected` (401, nothing stored)
- unavailable provider → `test_unknown_provider_is_rejected` (403)
- alert expires → `test_expired_alert_never_refires_after_snooze` (expiry beats the snooze re-nag)
- quiet mode → `test_quiet_hours_delay_instead_of_drop` + `test_quiet_delay_maths` (cross-midnight, malformed setting never silences)
- screen readability → title ≤60 chars enforced by the model; device card clipping is the existing alert UI (28/16 chars) — not re-proven on hardware here
- manual opt-out and reset → `test_disabled_or_unconfigured_fails_closed` (503 when off/unconfigured), `test_owner_status_and_reset_are_bearer_protected` (401 without token; reset clears ledger; secret never echoed)
- privileged-action injection → `test_injected_privileged_fields_are_ignored`
- oversized unauthenticated body → `test_oversized_body_is_refused_before_signature_check` (16 KB header cap; chunked bypass noted in code)

## Not proven (honest)

- No real Home Assistant instance or parcel service connected — synthetic payloads only, as the
  prompt requires. No broad HA compatibility claim.
- No firmware/device change; alert rendering on the Cardputer is the existing path, not re-flashed.
- Quiet hours delay events; they do not batch or summarise.
