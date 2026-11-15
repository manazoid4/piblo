import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { normalizeMeter } from "../contract.mjs";
import { plan } from "../planner.mjs";
import { parseLocal, addLocalDays, nextReset, parseWindow } from "../timez.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const NOW = Date.parse("2026-11-15T18:30:00Z");
const app = (o) => normalizeMeter({ provider: "claude", provider_kind: "APP", price: "subscription-prepaid", confidence: "manual",
  window: "5h", limit: 100, remaining: 40, checked_at: "2026-11-15T18:00:00Z", ...o }, { now: NOW });
const local = normalizeMeter({ provider: "ollama", provider_kind: "LOCAL", price: "local", verified_free: true }, { now: NOW });

test("normal window: runs now within reserve, next window is the reset, overnight splits at reset", () => {
  const p = plan([app({ reset_at: "2026-11-15T23:00:00Z" }), local], { now: NOW });
  assert.equal(p.can_run_now[0].budget_units, 32); // 40 * 0.8
  assert.equal(p.next_window_opens.at, "2026-11-15T23:00:00.000Z");
  assert.equal(p.overnight.length, 2);
  assert.equal(p.overnight[1].lanes.find(l => l.id === "claude:5h").assumed_refill, true);
  assert.equal(p.overnight[1].lanes.find(l => l.id === "claude:5h").budget_units, 80); // refilled to limit * 0.8
  assert.match(p.summary, /never paid/);
});
test("exhausted meter is blocked until its reset, local lane keeps running overnight", () => {
  const p = plan([app({ remaining: 0, reset_at: "2026-11-15T23:00:00Z" }), local], { now: NOW });
  assert.equal(p.can_run_now.length, 1);
  assert.equal(p.can_run_now[0].lane, "local");
  assert.equal(p.blocked[0].reason, "exhausted");
  assert.equal(p.blocked[0].opens_at, "2026-11-15T23:00:00.000Z");
  assert.equal(p.overnight[0].lanes.length, 1);
  assert.equal(p.overnight[1].lanes.length, 2);
});
test("reset already in the past: assume refill, roll next reset forward by the window", () => {
  const p = plan([app({ remaining: 0, checked_at: "2026-11-15T15:30:00Z", reset_at: "2026-11-15T17:30:00Z" })], { now: NOW });
  assert.equal(p.can_run_now.length, 1);
  assert.equal(p.can_run_now[0].assumed_refill, true);
  assert.equal(p.can_run_now[0].confidence, "low");
  assert.equal(p.can_run_now[0].reset_at, "2026-11-15T22:30:00.000Z"); // 17:30 + 5h
});
test("reset in the past but limit unknown: refuses to guess", () => {
  const p = plan([app({ limit: null, remaining: 0, checked_at: "2026-11-15T15:30:00Z", reset_at: "2026-11-15T17:30:00Z" })], { now: NOW });
  assert.equal(p.can_run_now.length, 0);
  assert.match(p.blocked[0].reason, /limit unknown/);
});
test("missing reset: usable now, no invented schedule; exhausted without reset asks for input", () => {
  const ok = plan([app({ reset_at: null })], { now: NOW });
  assert.equal(ok.can_run_now.length, 1);
  assert.equal(ok.overnight.length, 1);
  assert.equal(ok.next_window_opens, null);
  const dead = plan([app({ remaining: 0, reset_at: null })], { now: NOW });
  assert.equal(dead.can_run_now.length, 0);
  assert.equal(dead.next_window_opens, null);
  assert.equal(dead.needs_input.length, 1);
  assert.match(dead.summary, /Next window: none known/);
});
test("DST spring forward: daily 23:00 local reset stays 23:00 local (22:00Z after the change)", () => {
  const now = Date.parse("2026-03-28T22:00:00Z");
  const m = normalizeMeter({ provider: "x", provider_kind: "APP", price: "subscription-prepaid", window: "daily", limit: 10, remaining: 5,
    reset_local: "2026-03-28T23:00", checked_at: "2026-03-28T21:00:00Z" }, { now });
  const p = plan([m], { now, hours: 30 });
  assert.deepEqual(p.overnight.map(s => s.from), ["2026-03-28T22:00:00.000Z", "2026-03-28T23:00:00.000Z", "2026-03-29T22:00:00.000Z"]);
  assert.match(p.overnight[2].from_local, /23:00 BST/);
});
test("DST autumn fall back: daily 23:00 local reset is 23:00Z after the change", () => {
  const now = Date.parse("2026-10-24T20:00:00Z");
  const m = normalizeMeter({ provider: "x", provider_kind: "APP", price: "subscription-prepaid", window: "daily", limit: 10, remaining: 5,
    reset_local: "2026-10-24T23:00", checked_at: "2026-10-24T19:00:00Z" }, { now });
  const cuts = plan([m], { now, hours: 30 }).overnight.map(s => s.from);
  assert.deepEqual(cuts, ["2026-10-24T20:00:00.000Z", "2026-10-24T22:00:00.000Z", "2026-10-25T23:00:00.000Z"]);
});
test("timez helpers: weekly step across DST keeps wall clock; bad windows rejected", () => {
  const a = parseLocal("2026-03-27T09:00", "Europe/London");
  assert.equal(new Date(addLocalDays(a, 7, "Europe/London")).toISOString(), "2026-04-03T08:00:00.000Z");
  assert.equal(parseWindow("monthly"), null);
  assert.equal(nextReset(a, null, a + 1, "Europe/London"), null);
});
test("paid, stale, unverified and bad-ID meters never enter any lane", () => {
  const bad = [
    normalizeMeter({ provider: "o", provider_kind: "API", price: "0.001", verified_free: true, window: "daily", limit: 5, remaining: 5, checked_at: new Date(NOW).toISOString() }, { now: NOW }),
    app({ checked_at: "2026-11-15T01:00:00Z", reset_at: "2026-11-16T01:00:00Z" }),
    normalizeMeter({ provider: "g", provider_kind: "API", price: "free", window: "daily", limit: 5, remaining: 5, checked_at: new Date(NOW).toISOString() }, { now: NOW }),
    normalizeMeter({ provider: "e", provider_kind: "API", price: "free", verified_free: true, model_id: "x:free\n\u001b[31m", window: "daily", limit: 5, remaining: 5, checked_at: new Date(NOW).toISOString() }, { now: NOW }),
  ];
  const p = plan(bad, { now: NOW });
  assert.equal(p.can_run_now.length, 0);
  assert.ok(p.overnight.every(s => s.lanes.length === 0));
  assert.equal(p.blocked.length, 4);
});
test("cooldown keeps a meter out of lanes until it expires", () => {
  const m = app({ reset_at: "2026-11-15T23:00:00Z" });
  const p = plan([m], { now: NOW, cooling: { claude: NOW + 3_600_000 } });
  assert.equal(p.can_run_now.length, 0);
  assert.equal(p.overnight[0].lanes.length, 0);
});
test("bad inputs throw", () => {
  assert.throws(() => plan([], { tz: "Mars/Base" }), /timezone/);
  assert.throws(() => plan([], { hours: 500 }), /hours/);
});
test("CLI prints JSON + summary from the example snapshot", () => {
  const r = spawnSync(process.execPath, [join(root, "planner.mjs"), "--snapshot", join(root, "tests", "manual-snapshot.example.json"), "--now", "2026-11-15T18:30:00Z", "--json"], { encoding: "utf8", timeout: 8000 });
  assert.equal(r.status, 0, r.stderr);
  const j = JSON.parse(r.stdout);
  assert.equal(j.next_window_opens.at, "2026-11-15T23:00:00.000Z");
  assert.ok(j.can_run_now.some(x => x.lane === "local"));
});
