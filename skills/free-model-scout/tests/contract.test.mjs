import { test } from "node:test";
import assert from "node:assert/strict";
import { writeFile, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { KINDS, SAFE_ID, normalizeMeter, reconcile, loadSnapshot, metersFromUsage, zeroSpend } from "../contract.mjs";
import { fromOpenRouter, fromGroqHeaders, readOnlyStatus } from "../usage.mjs";

const NOW = Date.parse("2026-11-15T18:30:00Z");
const base = { provider: "p", provider_kind: "API", price: "free", verified_free: true, window: "daily", limit: 10, remaining: 4, used: 6, checked_at: "2026-11-15T18:20:00Z" };
const n = (o) => normalizeMeter({ ...base, ...o }, { now: NOW });

test("contract exposes every required field", () => {
  const m = n({});
  for (const k of ["provider_kind", "model_id", "task", "verified_free", "price", "used", "remaining", "limit", "window", "reset_at", "checked_at", "confidence", "privacy", "eligibility", "unknown_reason", "evidence_url"])
    assert.ok(k in m, k);
  assert.deepEqual(KINDS, ["API", "APP", "LOCAL", "MANUAL"]);
  assert.equal(m.eligibility, "eligible");
});
test("malicious model IDs are rejected, not echoed", () => {
  for (const id of ["a b", "x\n:free", "\u001b[2Jx", "../../etc", "a".repeat(200), "x:free\u202e", "$(rm -rf)"]) {
    const m = n({ model_id: id });
    assert.equal(m.eligibility, "blocked", id);
    assert.equal(m.model_id, null);
  }
  assert.equal(SAFE_ID.test("meta-llama/llama-3.3-70b-instruct:free"), true);
  assert.equal(n({ model_id: "meta/ok:free" }).model_id, "meta/ok:free");
});
test("price whitelist: anything chargeable or unparseable is blocked", () => {
  for (const price of ["0.001", "$0", "free ", "paid", null, undefined, "1e-9"]) assert.equal(zeroSpend({ price }), false, String(price));
  assert.equal(zeroSpend({ price: 0 }), true);
  assert.equal(n({ price: "0.5" }).eligibility, "blocked");
  assert.equal(n({ price: undefined }).eligibility, "blocked");
});
test("stale counters become unknown; a passed reset supersedes old counters", () => {
  assert.equal(n({ checked_at: "2026-11-15T10:00:00Z" }).unknown_reason, "stale counters");
  assert.equal(n({ checked_at: "2026-11-15T10:00:00Z", reset_at: "2026-11-15T12:00:00Z" }).eligibility, "eligible");
  assert.equal(n({ checked_at: null }).unknown_reason, "no checked_at");
  assert.equal(n({ checked_at: "2026-11-16T09:00:00Z" }).unknown_reason, "checked_at in the future");
  assert.equal(n({ provider_kind: "LOCAL", price: "local", checked_at: null }).eligibility, "eligible");
});
test("inconsistent counters are unknown with low confidence", () => {
  const m = n({ remaining: 12, used: 0 });
  assert.equal(m.eligibility, "unknown");
  assert.equal(m.confidence, "low");
  assert.equal(n({ remaining: 3, used: 3 }).unknown_reason, "counters inconsistent");
  assert.equal(n({ remaining: -2 }).remaining, null);
});
test("timestamps without an offset are rejected; reset_local + tz is converted", () => {
  assert.equal(n({ reset_at: "2026-11-15T23:00:00" }).reset_at, null);
  assert.equal(n({ reset_at: undefined, reset_local: "2026-07-01T23:00", tz: "Europe/London" }).reset_at, "2026-07-01T22:00:00.000Z");
  assert.equal(n({ reset_local: "garbage" }).reset_at, null);
});
test("API meters need verified_free; unverified free tier stays unknown", () => {
  const m = n({ verified_free: false });
  assert.equal(m.eligibility, "unknown");
  assert.match(m.unknown_reason, /unverified/);
});
test("sources that disagree: lower remaining wins, downgraded to unknown/low", () => {
  const r = reconcile([n({ remaining: 9, used: 1 }), n({ remaining: 2, used: 8 })]);
  assert.equal(r.length, 1);
  assert.equal(r[0].remaining, 2);
  assert.equal(r[0].confidence, "low");
  assert.equal(r[0].eligibility, "unknown");
  assert.equal(reconcile([n({}), n({})]).length, 1);
});
test("account missing is unknown, never eligible, and asks for the key", () => {
  const [m] = metersFromUsage(readOnlyStatus("groq"), { now: NOW });
  assert.equal(m.eligibility, "unknown");
  const [o] = metersFromUsage({ provider: "openrouter", configured: false, reason: "API key env var not set" }, { now: NOW });
  assert.match(o.unknown_reason, /account missing/);
});
test("usage converters: OpenRouter verified counters eligible; Groq headers give reset_at but stay unverified", () => {
  const at = new Date(NOW).toISOString();
  const [o] = metersFromUsage({ provider: "openrouter", checked_at: at, quota_source: "https://openrouter.ai/api/v1/key", ...fromOpenRouter({ data: { free_model_daily_requests: { limit: 1000, remaining: 843, used: 157 } } }) }, { now: NOW });
  assert.equal(o.eligibility, "eligible");
  assert.equal(o.remaining, 843);
  const g = metersFromUsage({ provider: "groq", checked_at: at, ...fromGroqHeaders({ "x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "999", "x-ratelimit-reset-requests": "1m30s", "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "7900", "x-ratelimit-reset-tokens": "7.5s" }) }, { now: NOW });
  assert.equal(g[0].reset_at, new Date(NOW + 90_000).toISOString());
  assert.ok(g.every(x => x.eligibility === "unknown"));
});
test("manual APP snapshot loads with prepaid defaults and manual confidence; junk rows are blocked", async () => {
  const dir = await mkdtemp(join(tmpdir(), "scout-"));
  const f = join(dir, "snap.json");
  await writeFile(f, JSON.stringify({ tz: "Europe/London", meters: [
    { provider: "claude", window: "5h", limit: 100, remaining: 40, reset_local: "2026-11-15T23:00", checked_at: "2026-11-15T18:00:00Z" },
    { provider: "chatgpt", provider_kind: "APP", price: "20.00", window: "5h", limit: 1, remaining: 1, checked_at: "2026-11-15T18:00:00Z" },
    { provider: "x", provider_kind: "ROOT" },
  ] }));
  const [a, b, c] = await loadSnapshot(f, { now: NOW });
  assert.equal(a.provider_kind, "APP");
  assert.equal(a.eligibility, "eligible");
  assert.equal(a.confidence, "manual");
  assert.equal(a.reset_at, "2026-11-15T23:00:00.000Z");
  assert.equal(b.eligibility, "blocked");
  assert.equal(c.eligibility, "blocked");
});
