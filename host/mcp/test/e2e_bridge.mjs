// e2e bridge: one tool call against the real piblo-mcp over stdio, from the pytest driver.
// Usage: node e2e_bridge.mjs <PIBLO_URL> <PIBLO_TOKEN> <tool> <json-args>
// Prints a single JSON line to stdout: { text, isError }. Exit 0 on a clean call, 1 on failure.
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";

const ENTRY = join(dirname(fileURLToPath(import.meta.url)), "..", "dist", "index.js");
const [, , url, token, tool, argsJson] = process.argv;

const transport = new URL(url).pathname === "/mcp"
  ? new StreamableHTTPClientTransport(new URL(url), { requestInit: { headers: { Authorization: `Bearer ${token}` } } })
  : new StdioClientTransport({
  command: process.execPath,
  args: [ENTRY],
  env: { PATH: process.env.PATH ?? "", PIBLO_URL: url, PIBLO_TOKEN: token, PIBLO_AGENT_NAME: "e2e-test" },
  });
const client = new Client({ name: "e2e-test", version: "1.0.0" });

try {
  await client.connect(transport);
  const r = await client.callTool({ name: tool, arguments: JSON.parse(argsJson) });
  const text = r.content.map((c) => c.text).join("\n");
  process.stdout.write(JSON.stringify({ text, isError: r.isError === true }) + "\n");
  process.exit(0);
} catch (err) {
  process.stdout.write(JSON.stringify({ text: String(err?.message ?? err), isError: true }) + "\n");
  process.exit(1);
} finally {
  try { await client.close(); } catch { /* best effort */ }
}
