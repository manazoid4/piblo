# piblo-proto v1 — Core ↔ device / simulator / agents wire contract

The protocol between Piblo **Core** (the Python FastAPI server in `host/mazhost`) and its clients —
the Cardputer firmware, the browser/headless simulator, the Android app, and MCP tools (Claude Code,
Codex, …). Every client speaks the same HTTP+JSON routes under `/buddy/*` and identifies itself with
the optional header `X-Piblo-Client: <firmware|simulator|android|mcp>/<version>`.

**Auth:** all `/buddy/*` routes require `Authorization: Bearer <MAZ_TOKEN>` except the static pages
(`/ui`, `/sim`, `/health`). Nothing here ever carries the token in a URL or body.

**Additive only.** Older clients ignore unknown fields and unknown event `kind`s. Do not change an
existing field's meaning; add a new field or route instead.

---

## Who calls what

| Client | Polls | Posts |
|---|---|---|
| Firmware / simulator | `GET /buddy/events?since=N`, `GET /buddy/summary`, `GET /buddy/status` | `POST /buddy/decide`, `POST /buddy/alert/{id}/{ack\|snooze}`, `POST /buddy/device-state` |
| Agent / MCP tool | — | `POST /buddy/request` (approval), `POST /buddy/alert`, `POST /buddy/event`, `POST /buddy/task` |
| Agent / MCP (blocking) | `GET /buddy/state?id=…&wait=…` | `POST /buddy/decide` (cancel) |

## Events ring (`GET /buddy/events?since=N`)

`{ "ok": true, "events": [{ "id", "kind", "text", "source", "ts", … }], "last": <max id> }`

Kinds the device understands: `alert` (with an `alert` object), `done`, `usage`, `needs`, `error`.
New kinds are ignored by old firmware.

## Approval (`POST /buddy/request`, `GET /buddy/state`, `POST /buddy/decide`)

- **Fail closed.** No answer in 60 s → expired. A timeout, lost connection, or restart = rejected.
- Only Y/Enter approves on the device; there is no shortcut and untrusted text can never approve.
- Idempotency: repeat a request with the same `idempotency_key` and Core returns the original record
  (`duplicate: true`) instead of creating a second prompt.
- `sensitive: true` marks a request for the hold-to-approve path (Nod, PR #63) where the device
  supports it; the flag is otherwise recorded only.
- `GET /buddy/state?id=…` returns `{ id, decision, known, expired? }`. `decision` is one of
  `null` (waiting) / `allow` / `deny` / `cancel` / `expired`.

## Alerts (`POST /buddy/alert`, `POST /buddy/alert/{id}/ack|snooze`)

See [ALERTS.md](ALERTS.md). `kind: approval` routes into the approval queue. Added here (all optional):
`agent`, `idempotency_key`, `sensitive`.

## Additions in this PR (feat/mcp-companion), all additive

| Route | Method | Purpose |
|---|---|---|
| `/buddy/task` | POST | Agent reports task status (`waiting|running|done|failed` + progress %). First `done`/`failed` also raises one alert, so firmware needs no change. |
| `/buddy/tasks` | GET | Saved tasks. |
| `/buddy/status` | GET | Is a client connected, simulated, last-seen, device battery/connectivity, halt state. |
| `/buddy/device-state` | POST | A client reports its own battery/charging/wifi. |
| `/buddy/demo` | POST | "Test Piblo": one demo notification + one demo approval. |
| `/buddy/history` | GET | Durable notifications, approvals, alerts (sqlite). |
| `/buddy/agents` | GET | Known agents (friendly name, client kind, capabilities, last seen). |
| `/buddy/activity` | GET | Error / recovery log. |
| `/buddy/prefs` | GET/PUT | Retention days, shortcuts. |
| `/buddy/export` | GET | Full JSON export (no secrets, no token). |
| `/buddy/reset` | POST | Clear saved history (needs `confirm: true`). |
| `/buddy/connect-info` | GET | Copy-paste MCP config for Claude Code and Codex. |

`/buddy/request` and `/buddy/alert` gained optional `agent`, `idempotency_key`, `sensitive` fields.

## Hermes agent routes (feat: Hermes gadget port, additive)

Core can hand ordinary questions to the owner's Hermes Agent over the Hermes Gadget protocol
(docs/plans/2026-11-20-hermes-gadget-port.md). Firmware needs no change: answers still come back
from `/turn` / `/turn/raw` / `/turn/text` (`provider: "hermes"`, optional `cards`, `degraded`), and
Hermes questions arrive as ordinary approvals (`agent: "Hermes"`) decided with `/buddy/decide`.

| Route | Method | Purpose |
|---|---|---|
| `/hermes/status` | GET | Enabled, selected agent, connected, paired, pairing code, last error, dashboard. Never the key or token. |
| `/hermes/agent` | POST | `{"agent": "piblo"\|"hermes"}`: who answers ordinary questions. |
| `/hermes/ask` | POST | `{"text"}`: one Hermes turn (app/testing). |
| `/hermes/model` | POST | `{"model", "provider"?}`: sends Hermes `/model provider:model` (OpenRouter names resolve, free first). |
| `/hermes/cancel`, `/hermes/new` | POST | Stop the Hermes turn; start a fresh Hermes conversation. |

## Persistence rules

- Alerts, approvals, notifications, tasks, agents, connections, prefs and activity are written through
  to a sqlite DB (`buddy.sqlite3`, WAL, versioned migrations, bounded tables + retention).
- **Approvals are never replayed.** On open, every still-pending approval is marked `expired`
  (`decided_by: "restart"`). Reloaded alerts are marked fired+expired so a restart never re-notifies.
- A corrupt DB is moved aside to `<name>.corrupt-<n>` and a fresh store starts, logging the event.
- Event ids keep rising across restarts, so a polling device never goes stale.
