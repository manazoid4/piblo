import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { State, SpendBlocked, assertZeroSpend, afterHttp, orderCandidates, retrySeconds } from "../guard.mjs";
import { normalize, eligible } from "../scout.mjs";

const mk = async () => new State(await mkdtemp(join(tmpdir(), "scout-state-")));
const NOW = Date.parse("2026-11-15T18:30:00Z");

test("assertZeroSpend blocks paid/unverified status and malicious IDs", () => {
  assert.ok(assertZeroSpend({ id: "a/b:free", status: "catalog-zero-cost" }));
  assert.ok(assertZeroSpend({ id: "qwen3:4b", status: "local-unmetered" }));
  for (const status of ["paid-or-unknown", "unverified-account-tier", "ollama-cloud-unverified", undefined])
    assert.throws(() => assertZeroSpend({ id: "a/b:free", status }), SpendBlocked);
  assert.ok(assertZeroSpend({ id: "g/m", status: "unverified-account-tier" }, { includeUnverified: true }));
  for (const id of ["x:free\n", "\u001b]0;pwn\u0007:free", "a b:free", "", null, "y".repeat(300)])
    assert.throws(() => assertZeroSpend({ id, status: "catalog-zero-cost" }), SpendBlocked);
});
test("malicious model IDs are dropped at catalog parse for every provider", () => {
  const evil = ["ok/model:free", "bad\nid:free", "\u001b[31mred:free", "sp ace:free"];
  const or = normalize("openrouter", { data: evil.map(id => ({ id, pricing: { prompt: "0", completion: "0" } })) });
  assert.deepEqual(or.map(m => m.id), ["ok/model:free"]);
  assert.deepEqual(normalize("groq", { data: evil.map(id => ({ id })) }).map(m => m.id), ["ok/model:free"]);
  assert.deepEqual(normalize("ollama", { models: evil.map(name => ({ name })) }).map(m => m.id), ["ok/model:free"]);
});
test("429 starts a cooldown from Retry-After; clamps absurd values; 401/402 cool 24h; 200 does nothing", async () => {
  const s = await mk();
  assert.deepEqual(await afterHttp(s, "groq", 200), { stop: false });
  assert.equal((await afterHttp(s, "groq", 429, { "Retry-After": "90" })).cooldown_s, 90);
  assert.equal(await s.active("groq"), "HTTP 429");
  assert.equal((await afterHttp(s, "nvidia", 429, { "retry-after": "99999999" })).cooldown_s, 86400);
  assert.equal((await afterHttp(s, "or", 402)).cooldown_s, 86400);
  assert.equal((await afterHttp(s, "x", 401)).stop, true);
  assert.equal(retrySeconds("garbage"), 300);
  assert.equal(retrySeconds("-5"), 300);
  assert.equal(retrySeconds("1m30s"), 90);
  assert.equal(retrySeconds("Sun, 15 Nov 2026 18:31:00 GMT", NOW), 60);
});
test("cooldown expires; planner map only has live entries", async () => {
  const s = await mk();
  await s.cool("groq", 10, "HTTP 429", NOW);
  assert.equal(await s.active("groq", NOW + 5_000), "HTTP 429");
  assert.equal(await s.active("groq", NOW + 11_000), null);
  assert.deepEqual(Object.keys(await s.cooling(NOW + 5_000)), ["groq"]);
  assert.deepEqual(await s.cooling(NOW + 20_000), {});
});
test("corrupt or stale state files do not crash and do not invent cooldowns", async () => {
  const s = await mk();
  await writeFile(s.file, "{not json");
  assert.deepEqual(await s.cooling(), {});
  await writeFile(s.file, JSON.stringify({ cooldown: { groq: { until: 1, reason: "old" } }, limits: null }));
  assert.equal(await s.active("groq"), null);
  await writeFile(s.file, JSON.stringify({ cooldown: "x" }));
  assert.deepEqual(await s.cooling(), {});
});
test("rate-limit headers are stored, other headers (auth, cookies) are not", async () => {
  const s = await mk();
  await s.limits("groq", { "x-ratelimit-remaining-requests": "5", Authorization: "Bearer SECRET", "set-cookie": "a=b", "retry-after": "3" });
  const raw = await readFile(s.file, "utf8");
  assert.match(raw, /x-ratelimit-remaining-requests/);
  assert.doesNotMatch(raw, /SECRET|cookie|a=b/i);
});
test("ledger is metadata only: no prompt/output/key fields, unsafe IDs refused", async () => {
  const s = await mk();
  assert.equal(await s.record({ provider: "openrouter", id: "a/b:free", task: "code", success: true, status: "200", latency_ms: 812,
    input_tokens: 12, output_tokens: 3, prompt: "SECRET PROMPT", output: "SECRET OUT", api_key: "sk-123" }), true);
  assert.equal(await s.record({ provider: "openrouter", id: "bad\nid", success: true }), false);
  const raw = await readFile(s.ledger, "utf8");
  assert.doesNotMatch(raw, /SECRET|sk-123|prompt|output"/);
  const rows = await s.rows();
  assert.equal(rows.length, 1);
  assert.deepEqual(Object.keys(rows[0]).sort(), ["at", "id", "input_tokens", "latency_ms", "output_tokens", "provider", "status", "success", "task"]);
  await writeFile(s.ledger, raw + "garbage line\n");
  assert.equal((await s.rows()).length, 1); // garbage skipped
});
test("ordering: measured success/latency beat heuristic; cooled providers dropped; no hidden paid entries", () => {
  const models = [
    { provider: "openrouter", id: "big:free", task: "code", heuristic_score: 90, status: "catalog-zero-cost" },
    { provider: "openrouter", id: "fast:free", task: "code", heuristic_score: 10, status: "catalog-zero-cost" },
    { provider: "openrouter", id: "slow:free", task: "code", heuristic_score: 20, status: "catalog-zero-cost" },
    { provider: "groq", id: "g/m", task: "code", heuristic_score: 99, status: "unverified-account-tier" },
  ];
  const row = (id, success, latency_ms) => ({ provider: "openrouter", id, task: "code", success, latency_ms });
  const rows = [row("fast:free", true, 300), row("fast:free", true, 320), row("slow:free", true, 3000), row("slow:free", true, 2900), row("big:free", false, null)];
  const out = orderCandidates(models, rows, { cooling: { groq: NOW + 1000 }, now: NOW });
  assert.deepEqual(out.map(m => m.id), ["fast:free", "slow:free", "big:free"]);
  assert.deepEqual(out.map(m => m.rank_basis), ["measured", "measured", "heuristic"]);
  assert.equal(out[0].measured.median_latency_ms, 320);
  assert.deepEqual(eligible(models).length, 3);
});
test("a single lucky run is not 'measured'", () => {
  const out = orderCandidates([{ provider: "p", id: "a", task: "t", heuristic_score: 1 }, { provider: "p", id: "b", task: "t", heuristic_score: 5 }],
    [{ provider: "p", id: "a", task: "t", success: true, latency_ms: 5 }]);
  assert.deepEqual(out.map(m => m.id), ["b", "a"]);
});
