# Piblo × ChatGPT — build and display handover (15 Nov 2026)

## ChatGPT bridge — implemented on this branch

This branch adds an OPTIONAL ChatGPT-facing adapter to host/mcp, without changing Cardputer firmware, Piblo Core, or the existing stdio MCP entry for Claude and Codex.

- host/mcp/src/remote.ts — authenticated Streamable HTTP MCP on localhost port 8791, with distinct 32+ character secret, 256 KiB request cap, origin guard, and healthz.
- host/mcp/src/chatgpt.ts — reuses five existing MCP tools, adds read-only piblo_overview and a tiny MCP Apps UI resource. No Core credentials or arbitrary commands in the iframe.
- host/mcp/test/remote.test.mjs — integration tests for auth, MCP handshake, tool listing, and widget resource. Tests require Node and installed repo dependencies.

Start Piblo Core as usual (default localhost:8787). On Windows PowerShell, from repository root:

    cd host/mcp
    npm ci
    npm run build
    $env:PIBLO_REMOTE_TOKEN = (python -c "import secrets; print(secrets.token_urlsafe(48))")
    node dist/remote.js

MCP URL: http://127.0.0.1:8791/mcp. Use Authorization: Bearer with the separately generated PIBLO_REMOTE_TOKEN. This is a developer-only single-user bridge, not a public multi-tenant app. Do not expose Piblo Core or port 8787; use supported Secure MCP Tunnel or a trusted authenticated HTTPS tunnel for remote testing. Production requires OAuth, account/device isolation, per-user permissions and revocation. ChatGPT custom-app support depends on account/workspace access; this does not attach to any existing ChatGPT web conversation by itself.

Realtime voice stays on the direct Cardputer → Core → speech/AI provider path. MCP is for control, not speech.


Display-board notes live in the private repo.
