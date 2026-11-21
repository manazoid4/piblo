#!/usr/bin/env node
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { CoreClient, loadConfig } from "./core.js";
import { createServer } from "./server.js";
import { createHttpServer } from "./http.js";

const cfg = loadConfig();
if (!cfg.token) console.error("piblo-mcp: no token found (PIBLO_TOKEN or host/.env); tools will report no_token");

if (process.argv.includes("--http")) {
  if (!cfg.token) { console.error("piblo-mcp: HTTP mode requires a Core token"); process.exit(1); }
  const port = Number(process.env.PIBLO_MCP_PORT || "8811");
  if (!Number.isInteger(port) || port < 1 || port > 65535) { console.error("piblo-mcp: invalid port"); process.exit(1); }
  const server = createHttpServer(new CoreClient(cfg), cfg.token);
  server.listen(port, "127.0.0.1", () => console.error(`piblo-mcp ready at http://127.0.0.1:${port}/mcp`));
  for (const sig of ["SIGINT", "SIGTERM"] as const) process.on(sig, () => server.close(() => process.exit(0)));
} else {
  // stdout is the protocol channel: log to stderr only.
  const handle = serveStdio(() => createServer(new CoreClient(cfg), cfg.agent));
  console.error(`piblo-mcp ready (Core ${cfg.url})`);
  for (const sig of ["SIGINT", "SIGTERM"] as const) process.on(sig, () => void handle.close().finally(() => process.exit(0)));
}
