// Unit tests for piblo-mcp against a scripted fake Core. The real Core is covered by host/tests/test_mcp_e2e.py.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { after, before, describe, test } from "node:test";
import { Client } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";
import { tokenFromEnvFile, urlAllowed } from "../dist/core.js";
import { toDecision } from "../dist/server.js";

const ENTRY = join(dirname(fileURLToPath(import.meta.url)), "..", "dist", "index.js");
const TOKEN = "unit-test-token";

/** Fake Core: records every request, answers /buddy/state from `script.states` in order. */
function fakeCore() {
  const seen = [];
  const script = { states: [], status: { connected: true, simulated: true, client: "simulator/1", last_seen_ago_s: 1.5, halted: false, device: { battery_pct: 77, charging: false, wifi_rssi: -50, secret: "no" } } };
  const server = createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      const body = raw ? JSON.parse(raw) : undefined;
      seen.push({ method: req.method, url: req.url, auth: req.headers.authorization, client: req.headers["x-piblo-client"], body });
      const json = (code, obj) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(obj)); };
      if (req.headers.authorization !== `Bearer ${TOKEN}`) return json(401, { detail: "authentication_required" });
      if (req.url.startsWith("/buddy/status")) return json(200, script.status);
      if (req.url === "/buddy/alert") return json(200, { ok: true, id: "al-1" });
      if (req.url === "/buddy/task") return json(200, { ok: true });
      if (req.url === "/buddy/request") return json(200, { id: "r1", decision: script.immediate ?? null });
      if (req.url.startsWith("/buddy/state")) return json(200, script.states.shift() ?? { id: "r1", decision: null });
      if (req.url === "/buddy/decide") return json(200, { ok: true });
      json(404, { detail: "nope" });
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, seen, script, url: `http://127.0.0.1:${server.address().port}` })));
}

async function connect(env, clientName = "unit-client") {
  const transport = new StdioClientTransport({ command: process.execPath, args: [ENTRY], env: { PATH: process.env.PATH ?? "", ...env } });
  const client = new Client({ name: clientName, version: "1.0.0" });
  await client.connect(transport);
  return client;
}
const call = async (client, name, args = {}) => {
  const r = await client.callTool({ name, arguments: args });
  return { ...JSON.parse(r.content[0].text), isError: r.isError === true };
};

describe("piblo-mcp", () => {
  let core, client;
  before(async () => {
    core = await fakeCore();
    client = await connect({ PIBLO_URL: core.url, PIBLO_TOKEN: TOKEN });
  });
  after(async () => { await client.close(); core.server.close(); });

  test("lists exactly the five tools, none of them a shell", async () => {
    const { tools } = await client.listTools();
    assert.deepEqual(tools.map((t) => t.name).sort(), ["piblo_device_state", "piblo_notify", "piblo_request_approval", "piblo_status", "piblo_task_update"]);
  });

  test("piblo_status reports simulated and sends the bearer token", async () => {
    const r = await call(client, "piblo_status");
    assert.equal(r.state, "simulated");
    assert.equal(core.seen.at(-1).auth, `Bearer ${TOKEN}`);
    assert.match(core.seen.at(-1).client, /^mcp\//);
  });

  test("piblo_device_state returns only permitted fields", async () => {
    const r = await call(client, "piblo_device_state");
    assert.deepEqual(Object.keys(r).sort(), ["battery_pct", "charging", "connected", "ok", "simulated", "wifi_rssi", "isError"].sort());
    assert.equal(r.battery_pct, 77);
  });

  test("piblo_notify posts an ack-only alert owned by the MCP client's name", async () => {
    const r = await call(client, "piblo_notify", { title: "Build finished", body: "all green", kind: "task_done", idempotency_key: "k1" });
    assert.equal(r.ok, true);
    const sent = core.seen.at(-1).body;
    assert.equal(sent.agent, "unit-client");
    assert.deepEqual(sent.actions, ["ack"]);
    assert.equal(sent.idempotency_key, "k1");
  });

  test("inputs are validated and length limited before they reach Core", async () => {
    const before = core.seen.length;
    const r = await client.callTool({ name: "piblo_notify", arguments: { title: "x".repeat(61) } });
    assert.equal(r.isError, true);
    const r2 = await client.callTool({ name: "piblo_notify", arguments: { title: "ok", kind: "approval" } });
    assert.equal(r2.isError, true); // approvals only via piblo_request_approval
    assert.equal(core.seen.length, before);
  });

  test("piblo_task_update forwards progress", async () => {
    await call(client, "piblo_task_update", { task_id: "t1", title: "Index repo", status: "running", progress: 30 });
    assert.deepEqual(core.seen.at(-1).body, { id: "t1", title: "Index repo", status: "running", progress: 30, detail: "", proof: "", agent: "unit-client" });
    const bad = await client.callTool({ name: "piblo_task_update", arguments: { task_id: "t1", title: "x", status: "running", progress: 150 } });
    assert.equal(bad.isError, true);
  });

  test("approval: allow -> approved, deny -> rejected, and sensitive asks for the Nod hold", async () => {
    core.script.states = [{ id: "r1", decision: "allow" }];
    const ok = await call(client, "piblo_request_approval", { action: "Run tests", sensitive: true });
    assert.equal(ok.decision, "approved");
    assert.equal(core.seen.find((s) => s.url === "/buddy/request" && s.body.sensitive).body.nod, true);
    core.script.states = [{ id: "r1", decision: "deny" }];
    assert.equal((await call(client, "piblo_request_approval", { action: "Delete build" })).decision, "rejected");
  });

  test("approval passes card fields and returns edit instruction and digest", async () => {
    core.script.states = [{ id: "r1", decision: "edit", instruction: "Revise it", digest: "abc" }];
    const result = await call(client, "piblo_request_approval", { action: "Send", category: "email", recipient: "Team", amount: "10 credits", undo: "Recall" });
    const sent = core.seen.filter((s) => s.url === "/buddy/request").at(-1).body;
    assert.equal(sent.category, "email");
    assert.equal(sent.recipient, "Team");
    assert.equal(sent.undo, "Recall");
    assert.equal(result.decision, "edit");
    assert.equal(result.instruction, "Revise it");
    assert.equal(result.digest, "abc");
  });

  test("approval: Core-side expiry and cancel both read as expired", async () => {
    core.script.states = [{ id: "r1", decision: "cancel", expired: true }];
    assert.equal((await call(client, "piblo_request_approval", { action: "Deploy" })).decision, "expired");
  });

  test("approval: no answer in time -> expired, and the request is cancelled in Core", async () => {
    core.script.states = [];
    const r = await call(client, "piblo_request_approval", { action: "Slow", wait_seconds: 5 });
    assert.equal(r.decision, "expired");
    assert.deepEqual(core.seen.at(-1).body, { id: "r1", decision: "cancel" });
  });

  test("toDecision fails closed on anything unknown", () => {
    assert.equal(toDecision({ decision: "allow_all" }), "rejected");
    assert.equal(toDecision({ decision: "allow" }), "approved");
    assert.equal(toDecision({ decision: null }), null);
  });
});

describe("piblo-mcp without a working Core", () => {
  test("unreachable Core: approval is rejected (fail closed), status says why", async () => {
    const client = await connect({ PIBLO_URL: "http://127.0.0.1:9", PIBLO_TOKEN: TOKEN, PIBLO_AGENT_NAME: "named-agent" });
    try {
      const r = await call(client, "piblo_request_approval", { action: "Anything" });
      assert.equal(r.decision, "rejected");
      assert.equal(r.error, "core_unreachable");
      assert.equal((await call(client, "piblo_status")).error, "core_unreachable");
    } finally { await client.close(); }
  });

  test("token is never sent over plain http to a public host (fail closed)", async () => {
    for (const ok of ["http://127.0.0.1:8787", "http://localhost:8787", "http://192.168.1.20:8787", "http://pc:8787",
      "http://100.101.1.2:8787", "https://core.example.com"]) assert.equal(urlAllowed(ok), true, ok);
    for (const bad of ["http://core.example.com", "http://8.8.8.8:8787", "ftp://127.0.0.1", "not a url"]) assert.equal(urlAllowed(bad), false, bad);
    const client = await connect({ PIBLO_URL: "http://core.example.com:8787", PIBLO_TOKEN: TOKEN });
    try {
      const r = await call(client, "piblo_request_approval", { action: "x" });
      assert.equal(r.decision, "rejected");
      assert.equal(r.error, "insecure_url");
    } finally { await client.close(); }
  });

  test("wrong token is reported as unauthorized, not as an approval", async () => {
    const core = await fakeCore();
    const client = await connect({ PIBLO_URL: core.url, PIBLO_TOKEN: "wrong" });
    try {
      const r = await call(client, "piblo_request_approval", { action: "x" });
      assert.equal(r.decision, "rejected");
      assert.equal(r.error, "unauthorized");
    } finally { await client.close(); core.server.close(); }
  });

  test("PIBLO_AGENT_NAME wins over the client name", async () => {
    const core = await fakeCore();
    const client = await connect({ PIBLO_URL: core.url, PIBLO_TOKEN: TOKEN, PIBLO_AGENT_NAME: "named-agent" });
    try {
      await call(client, "piblo_notify", { title: "hi" });
      assert.equal(core.seen.at(-1).body.agent, "named-agent");
    } finally { await client.close(); core.server.close(); }
  });

  test("token is read from a dotenv file and never from the URL", async () => {
    const dir = mkdtempSync(join(tmpdir(), "piblo-mcp-"));
    const file = join(dir, ".env");
    writeFileSync(file, `# comment\nMAZ_PORT=1\nMAZ_TOKEN="from-file"\n`);
    assert.equal(tokenFromEnvFile(file), "from-file");
    assert.equal(tokenFromEnvFile(join(dir, "missing.env")), "");
    const core = await fakeCore();
    const client = await connect({ PIBLO_URL: core.url, PIBLO_ENV_FILE: file });
    try {
      await call(client, "piblo_status");
      assert.equal(core.seen.at(-1).auth, "Bearer from-file");
    } finally { await client.close(); core.server.close(); }
  });
});
