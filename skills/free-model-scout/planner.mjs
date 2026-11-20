#!/usr/bin/env node
// Usage-window planner: what can run now, when the next window opens, and an unattended overnight schedule.
// Free / prepaid-subscription / local lanes only. There is no paid lane; zero-spend is enforced, not configured.
import { parseArgs } from "node:util";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { loadSnapshot, reconcile, zeroSpend } from "./contract.mjs";
import { State } from "./guard.mjs";
import { fmtLocal, nextReset, parseWindow, validZone } from "./timez.mjs";

const lane = (m) => m.provider_kind === "LOCAL" ? "local" : m.provider_kind === "API" ? "free-api" : "subscription";
export const meterId = (m) => [m.provider, m.model_id || m.task, m.window].filter(Boolean).join(":");

// State of one meter at instant t. cooling: { id -> untilMs } from guard cooldowns.
export function availability(m, t, tz, reserve, cooling = {}) {
  const id = meterId(m);
  if (!zeroSpend(m)) return { ok: false, reason: "not free or prepaid" };
  if (m.eligibility !== "eligible") return { ok: false, reason: m.unknown_reason || m.eligibility };
  const cool = cooling[id] ?? cooling[m.provider];
  if (cool && cool > t) return { ok: false, reason: "cooldown after provider limit/error", opens: cool };
  if (m.provider_kind === "LOCAL") return { ok: true, budget: null };
  const win = parseWindow(m.window), reset = m.reset_at ? Date.parse(m.reset_at) : null;
  const passed = reset !== null && reset <= t;
  const next = nextReset(reset, win, t, tz);
  let remaining = m.remaining, assumed = false;
  if (passed) { remaining = m.limit; assumed = true; } // window rolled over since the snapshot
  if (remaining === null) return { ok: false, reason: passed ? "window reset but limit unknown" : "remaining unknown", opens: next };
  const budget = Math.floor(remaining * (1 - reserve));
  if (remaining === 0 || budget < 1) return { ok: false, reason: remaining === 0 ? "exhausted" : "within reserve", opens: next };
  return { ok: true, budget, assumed, next };
}

export function plan(meters, { now = Date.now(), tz = "Europe/London", hours = 8, reserve = 0.2, cooling = {} } = {}) {
  if (!validZone(tz)) throw Error("unknown timezone");
  if (!(hours > 0 && hours <= 48) || !(reserve >= 0 && reserve < 1)) throw Error("hours 0-48, reserve 0-<1");
  const ms = reconcile(meters), until = now + hours * 3_600_000;
  const row = (m, a) => ({ id: meterId(m), lane: lane(m), budget_units: a.budget, remaining: m.remaining, reset_at: a.next ? new Date(a.next).toISOString() : m.reset_at,
    assumed_refill: !!a.assumed, confidence: a.assumed ? "low" : m.confidence });
  const nowRows = [], blocked = [], needs_input = [];
  for (const m of ms) {
    const a = availability(m, now, tz, reserve, cooling);
    if (a.ok) nowRows.push(row(m, a));
    else {
      blocked.push({ id: meterId(m), lane: lane(m), reason: a.reason, opens_at: a.opens ? new Date(a.opens).toISOString() : null });
      if (!a.opens && m.eligibility === "eligible") needs_input.push(meterId(m) + ": add reset_at/window");
    }
  }
  // Boundaries = every reset (rolled forward by its window) inside the horizon.
  const cuts = new Map(); // instant -> first meter id resetting then
  for (const m of ms) {
    if (m.eligibility !== "eligible" || !m.reset_at) continue;
    const win = parseWindow(m.window); let t = Date.parse(m.reset_at);
    if (t <= now) t = nextReset(t, win, now, tz) ?? Infinity;
    for (let i = 0; i < 24 && t < until; i++) { if (!cuts.has(t)) cuts.set(t, meterId(m)); t = nextReset(t, win, t, tz) ?? Infinity; }
  }
  const edges = [now, ...[...cuts.keys()].sort((a, b) => a - b).slice(0, 24), until];
  const overnight = [];
  for (let i = 0; i < edges.length - 1; i++) {
    const t = edges[i], lanes = [];
    for (const m of ms) {
      // A meter whose reset is at/before segment start is treated as refilled (assumed), once per window.
      const probe = { ...m };
      const r = m.reset_at ? Date.parse(m.reset_at) : null;
      if (r !== null && r <= t && m.provider_kind !== "LOCAL") {
        const win = parseWindow(m.window);
        probe.reset_at = new Date(nextReset(r, win, t, tz) ?? t + 1).toISOString();
        probe.remaining = m.limit; // refilled; the cap, not the old counter
        probe.eligibility = m.eligibility;
      }
      const a = availability(probe, t, tz, reserve, cooling);
      if (a.ok) lanes.push({ id: meterId(m), lane: lane(m), budget_units: a.budget, assumed_refill: r !== null && r <= t });
    }
    overnight.push({ from: new Date(t).toISOString(), to: new Date(edges[i + 1]).toISOString(),
      from_local: fmtLocal(t, tz), to_local: fmtLocal(edges[i + 1], tz), lanes,
      note: lanes.length ? "bounded jobs only; stop on 401/402/429" : "idle: nothing free available, wait for next reset" });
  }
  const opens = blocked.filter(b => b.opens_at).sort((a, b) => Date.parse(a.opens_at) - Date.parse(b.opens_at))[0] || null;
  const first = [...cuts.keys()].sort((a, b) => a - b)[0];
  const nw = opens ? { id: opens.id, at: Date.parse(opens.opens_at), why: "blocked meter reopens" }
    : first !== undefined ? { id: cuts.get(first), at: first, why: "next scheduled reset" } : null;
  const nextWindow = nw && { id: nw.id, at: new Date(nw.at).toISOString(), at_local: fmtLocal(nw.at, tz), why: nw.why };
  // Three plain lines. Names: meter id with ":" and "-" as spaces, first letter capitalised.
  const name = (id) => { const t = id.replace(/[:-]/g, " "); return t[0].toUpperCase() + t.slice(1); };
  const byId = new Map(ms.map(m => [meterId(m), m]));
  const nowTxt = nowRows.length ? nowRows.map(r => {
    if (r.budget_units === null) return name(r.id) + " (not metered)";
    const m = byId.get(r.id), of = r.assumed_refill ? m.limit : m.remaining;
    return `${name(r.id)} (${r.budget_units} of ${of}, ${of - r.budget_units} kept back)`;
  }).join(", ") : "nothing";
  const typed = ms.filter(m => m.confidence === "manual" && m.checked_at).map(m => Date.parse(m.checked_at)).filter(Number.isFinite);
  const summary = `Can run now: ${nowTxt}.
`
    + `Next refill: ${nextWindow ? name(nextWindow.id) + ", " + nextWindow.at_local : "not known"}.
`
    + `Tonight (${hours} h): ${overnight.length} ${overnight.length === 1 ? "block" : "blocks"}, split at each refill. Free, prepaid or local only, never paid.`
    + (typed.length ? ` You typed these numbers in at ${fmtLocal(Math.max(...typed), tz)}; Scout did not check them.` : "");
  return { schema_version: 1, now: new Date(now).toISOString(), tz, until: new Date(until).toISOString(), reserve,
    can_run_now: nowRows, blocked, next_window_opens: nextWindow, overnight, needs_input, summary };
}

async function main() {
  const { values } = parseArgs({ options: { snapshot: { type: "string" }, tz: { type: "string", default: "Europe/London" },
    hours: { type: "string", default: "8" }, reserve: { type: "string", default: "0.2" }, now: { type: "string" }, json: { type: "boolean", default: false } } });
  const now = values.now ? Date.parse(values.now) : Date.now();
  if (Number.isNaN(now)) throw Error("--now must be an ISO timestamp with offset");
  const meters = values.snapshot ? await loadSnapshot(values.snapshot, { now, tz: values.tz }) : [];
  const out = plan(meters, { now, tz: values.tz, hours: Number(values.hours), reserve: Number(values.reserve), cooling: await new State().cooling(now) });
  console.log(values.json ? JSON.stringify(out, null, 2) : out.summary);
}
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1]))
  main().catch(err => { console.error("Planner: " + String(err.message).slice(0, 200)); process.exitCode = 1; });
