# Promises (Promise Keeper) - additive Core endpoints

New, additive. No existing contract changes. A promise is a Buddy alert (`source="promise"`, `kind="deadline"`,
actions `ack`+`snooze`), so it reuses the existing durable store, scheduler, dedupe key and `/buddy/events` ring.
All routes sit behind the normal Core bearer token. Times are local (`PIBLO_TZ`, default `Europe/London`).

| Route | Purpose |
|---|---|
| `POST /promises/propose {note}` | Parse a note. Returns `candidates[{due,label}]`, `ambiguous`, `default_time` (09:00 assumed). Saves nothing. |
| `POST /promises/confirm {note,due,confirmed:true}` | Save the exact confirmed `due` (`YYYY-MM-DDTHH:MM`). Returns `due_label`. Same note+time is deduped (`duplicate:true`). |
| `GET /promises/today` | Up to 3 `cards` (`due`, `waiting`, `done`), each `{id,state,title<=28,when<=16,note,actions}`, plus `counts`. |
| `POST /promises/{id}/done` / `later {minutes}` / `undo` | Tactile actions. `undo` re-opens a done promise. |

Errors are 422 `{error,message}`: `no_date`, `invalid_date`, `invalid_year`, `invalid_time`, `in_the_past`,
`nonexistent_time` (clock change), `too_far` (alert scheduler holds 30 days), `not_confirmed`.
Also added: `Buddy.alert_undo` (re-open an acked alert).
