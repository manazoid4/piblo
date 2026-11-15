// End-to-end smoke test for the separate ChatGPT HTTP entry point.
// Reuses the same SDK dependency as the existing stdio tests; never requires real hardware.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, test } from "node:test";
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";

const ENTRY = join(dirname(fileURLToPath(import.meta.url)), "..", "dist", "remote.js");
const TOKEN = "piblo-remote-integration-test-token-123456";

async function startRemote() {
  const child = spawn(process.execPath, [ENTRY], {
    env: {
      ...process.env,
      PIBLO_REMOTE_HOST: "127.0.0.1",
      PIBLO_REMOTE_PORT: "0",
      PIBLO_REMOTE_TOKEN: TOKEN,
      PIBLO_URL: "http://127.0.0.1:1",
      PIBLO_TOKEN: "unit-core-token",
    },
    stdio: ["ignore", "ignore", "pipe"],
  });
  let output = "";
  const url = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("Remote MCP did not become ready: " + output.slice(0, 500))), 7000);
    child.once("exit", (code) => { clearTimeout(timeout); reject(new Error("Remote MCP exited: " + code + " " + output.slice(0, 500))); });
    child.stderr.on("data", (c) => {
      output += c.toString();
      const match = output.match(/piblo-mcp remote ready at (http:\/\/127\.0\.0\.1:\d+\/mcp)/);
      if (match) { clearTimeout(timeout); resolve(match[1]); }
    });
  });
  return { child, url };
}

describe("ChatGPT remote MCP", () => {
  test("refuses to start without a separate long remote bearer token", async () => {
    const child = spawn(process.execPath, [ENTRY], {
      env: { ...process.env, PIBLO_REMOTE_TOKEN: "", PIBLO_REMOTE_PORT: "0" },
      stdio: ["ignore", "ignore", "pipe"],
    });
    let error = "";
    child.stderr.on("data", (data) => { error += data.toString(); });
    const status = await new Promise((resolve) => child.once("exit", resolve));
    assert.notEqual(status, 0);
    assert.match(error, /PIBLO_REMOTE_TOKEN/);
  });

  test("requires bearer auth, rejects browser origins, and exposes UI read-only", async () => {
    const { child, url } = await startRemote();
    let client;
    try {
      const unauthorized = await fetch(url, { method: "POST" });
      assert.equal(unauthorized.status, 401);
      assert.match(unauthorized.headers.get("www-authenticate") ?? "", /^Bearer/);

      const origin = await fetch(url, {
        method: "POST",
        headers: { Authorization: "Bearer " + TOKEN, Origin: "https://evil.example" },
      });
      assert.equal(origin.status, 403);

      const large = await fetch(url, {
        method: "POST",
        headers: { Authorization: "Bearer " + TOKEN, "Content-Type": "application/json" },
        body: "x".repeat(256 * 1024 + 1),
      });
      assert.equal(large.status, 413);

      const transport = new StreamableHTTPClientTransport(new URL(url), {
        requestInit: { headers: { Authorization: "Bearer " + TOKEN } },
      });
      client = new Client({ name: "piblo-remote-smoke", version: "1.0.0" });
      await client.connect(transport);
      const { tools } = await client.listTools();
      assert(tools.some((tool) => tool.name === "piblo_overview"));
      assert(tools.some((tool) => tool.name === "piblo_notify"));
      assert(!tools.some((tool) => /shell|exec/.test(tool.name)));

      const result = await client.callTool({ name: "piblo_overview", arguments: {} });
      assert.equal(result.structuredContent.connected, false);
      const { resources } = await client.listResources();
      assert(resources.some((item) => item.uri === "ui://piblo/overview.html"));
      const ui = await client.readResource({ uri: "ui://piblo/overview.html" });
      assert.match(ui.contents[0].text, /Piblo Buddy/);
      assert.equal(ui.contents[0].mimeType, "text/html;profile=mcp-app");
    } finally {
      if (client) await client.close().catch(() => {});
      child.kill("SIGTERM");
    }
  });
});
