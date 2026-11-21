# Promise Keeper acceptance — 19/20 November 2026

Feature-build prompt: `piblo-private/prompts/feature-build/01-PROMISE-KEEPER-TODAY.md`.
The capture → confirm → reminder → Today cards → Done/Later/Undo flow already shipped
(#164, #165, #176, #187, #191). This round closes the remaining acceptance-test gaps and
proves the loop on the real Cardputer.

## What changed in this PR

- `compound_run.py`: a two-step voice promise now **fails closed** if a date proposal is
  ambiguous (two readings) instead of silently taking the first. Today's voice grammar only
  produces single-reading dates; the guard keeps "zero unconfirmed dates" true if the grammar
  grows (e.g. "next Friday").
- `test_compound.py`: +2 tests — ambiguous voice date never saved; voice approval expiry
  (90 s with no YES) runs nothing.

## Evidence

| # | Command | Output | Result |
|---|---|---|---|
| 1 | `python -m pytest tests -q` (host, clean `origin/main` 7723a31, before change) | 1006 passed, 3 skipped, 0 failed (exit 0) | PASS |
| 2 | `python -m pytest tests/test_compound.py tests/test_promises.py tests/test_promises_tonight.py -q` (after change) | 68 passed (exit 0), incl. the 2 new tests | PASS |
| 3 | `python -m pytest tests -q` (host, after change) | 1008 passed, 3 skipped, 0 failed (exit 0) | PASS |
| 4 | Real Cardputer ADV v1.5.4 over LAN via `scripts/piblo_remote.py` discovery: `POST /promises/confirm` (due +75 s, confirmed) → `GET /promises/today` → wait → physical `enter` → `GET /promises/today` | confirm 200 `al-4ac50fc4`; card `waiting` → fired → after Enter card `state:"done"`; screenshots `f01-device-alert.png`, `f01-device-done.png` (Home shown; the transient alert overlay itself was not caught in a still) | PASS (state proof; overlay still UNTESTED) |

Environment: Windows 11, Python 3.12.15 venv, pytest 9.2.0. Test temp on scratch dir
(`--basetemp`); C: drive was full at session start — test temp + package caches cleared first.

## Acceptance list vs coverage (prompt 01)

- 03/04/2027 ambiguity → `test_ambiguous_numeric_date_offers_both_readings_british_first` (existing)
- "next Friday" near DST → `test_next_friday_is_ambiguous_until_this_weeks_friday_has_passed`, `test_dst_labels_and_skipped_hour` (existing)
- invalid year → `test_bad_input_is_rejected_never_invented` (existing)
- duplicate webhook/click → `test_duplicate_click_and_note_make_one_promise` + Buddy idempotency (existing)
- clock drift → `test_flow_due_done_undo_later_and_cards` (stored epoch deadline fires as clock moves) + `test_in_n_hours_is_elapsed_time_across_the_clock_change` (existing)
- offline/reconnect, core reboot → `test_survives_core_restart_without_losing_or_renagging`, `test_restart_keeps_due_and_fired_for_a_fired_unanswered_promise`, `test_done_early_then_restart_then_undo_still_fires_later` (existing; lazy scheduler fires pending alerts on first poll after reconnect)
- duplicate notes → same dedupe test (existing)
- expired item → `test_expired_alert_never_fires` (buddy) + `overdue` flag asserted in cards test (existing)
- expired voice request → `test_expired_requests_cannot_be_approved` (buddy, existing) + **new** `test_voice_approval_expiry_runs_nothing` (runner)
- undo, empty Today → existing tests
- accessibility → app `TodayPromises.tsx` uses labelled input (`aria-label`) and real buttons; full screen-reader audit not done
- 240x135 constraint → card title/when clipped to 28/16 chars, asserted in cards test; device proof above
- ambiguous voice date → **new** `test_ambiguous_voice_date_is_never_silently_saved`

Not proven: nothing in this PR touches firmware; no flash, no Core restart.
