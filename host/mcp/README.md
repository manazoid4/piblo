# piblo-mcp

MCP server (stdio) that lets Claude Code, Codex, and other MCP apps notify you and ask for approval
through Piblo Core. It is a **thin adapter** over Core's HTTP API — no orchestration, no shell, no
notification ever becomes a command.

## Build & test

```
cd host/mcp
npm install        # or npm ci
npm test           # builds (tsc) then runs node --test (14 unit tests against a scripted fake Core)
```

## Connect an app

Open Core's `/sim` page, press **Connect**, then copy the block from **Connect an app** (it also lists
`claude mcp add …` and the Codex `config.toml`). The config points at `dist/index.js` and sets
`PIBLO_URL`; **the token is found automatically** — never pasted into the command.

The token is resolved in this order: `PIBLO_TOKEN` / `NOD_TOKEN` / `MAZ_TOKEN` env, then a dotenv file
(`PIBLO_ENV_FILE`, else `host/.env`). Core keeps its token in `host/.env`; no OS keyring dependency.

## Core-managed HTTP service

From the repository root run `npm --prefix host/mcp ci` and `npm --prefix host/mcp run build`.
Set `MAZ_MCP_PORT=8811` in Core's configuration before its next normal start.
Core needs Node.js on PATH and must be reachable on loopback. If Core runs from
an installed copy, set `MAZ_REPO_DIR` to the checkout containing the built bridge.
The default port setting is `0` (disabled).

Core starts the service at `http://127.0.0.1:8811/mcp` and stops it on shutdown.
Connect a Streamable HTTP client with `Authorization: Bearer <Core token>`.
Set `Accept: application/json, text/event-stream` and use
`Content-Type: application/json` for POST when calling it directly. Keep the
token in the client's local secret configuration. HTTP lists only
`piblo_status`, `piblo_device_state` and `piblo_devices`. When both devices
are unavailable, a typical `piblo_devices` result is:

```json
{"ok":true,"devices":[{"id":"primary","state":"offline","battery_pct":null},{"id":"display","state":"unknown","reason":"Display address not configured"}]}
```

Port binding stays on loopback. For later remote use, put a trusted HTTPS proxy
such as Tailscale Serve in front of it, preserve bearer authentication and
send a loopback Host upstream. If the Core is unreachable, tool calls return an error.

Kaggle's MCP endpoint (`https://www.kaggle.com/mcp`) is a separate client
connection. Configure it with Kaggle credentials in the client. Core makes
no requests to Kaggle and never receives its credentials.

## Tools

| Tool | What it does | Notes |
|---|---|---|
| `piblo_status` | Connected / simulated / last seen, halt state. | Read-only. |
| `piblo_notify` | Show a notification (info / task_done / reminder / deadline). | ack-only; nothing in it is run. |
| `piblo_task_update` | Report a task's status + progress %. | Same `task_id` for every update. |
| `piblo_request_approval` | Ask the user to approve/reject ONE action; waits ~55 s. | Returns `approved` / `rejected` / `expired`. Treat expired like rejected. `sensitive=true` asks for the Nod hold once PR #63 ships; until then it is a normal approve/reject. |
| `piblo_device_state` | Battery + connectivity only. | Allow-listed fields. |

Agent identity: `PIBLO_AGENT_NAME`, else the MCP app's announced client name. Length-limited and
zod-validated inputs; everything fails closed (unknown decision → rejected; unreachable Core → rejected).
