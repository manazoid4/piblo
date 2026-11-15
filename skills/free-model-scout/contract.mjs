// Provider/meter contract. One flat record per usable allowance; unknown stays unknown, never guessed.
// Kinds: API (read-only provider endpoint), APP (subscription app, manual snapshot only, never scraped),
//        LOCAL (on-box, hardware-limited), MANUAL (any other user-entered allowance).
import { readFile } from "node:fs/promises";
import { parseLocal, parseWindow, parseDuration, validZone } from "./timez.mjs";

export const KINDS = Object.freeze(["API", "APP", "LOCAL", "MANUAL"]);
export const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:\/@+-]{0,127}$/; // blocks control chars, spaces, ANSI, newlines
const FREE_PRICE = new Set(["free", "local", "subscription-prepaid"]); // anything else is chargeable = blocked
const MAX_AGE = { API: 3_600_000, APP: 21_600_000, MANUAL: 21_600_000, LOCAL: Infinity };
const ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:\d\d)$/;

const txt = (v, n = 160) => typeof v === "string"
  ? v.replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, " ").trim().slice(0, n) || null : null;
const count = (v) => { const n = Number(v); return v !== undefined && v !== null && v !== "" && Number.isSafeInteger(n) && n >= 0 ? n : null; };
const iso = (v) => typeof v === "string" && ISO.test(v) && !Number.isNaN(Date.parse(v)) ? new Date(Date.parse(v)).toISOString() : null;
export const zeroSpend = (m) => m?.price === 0 || FREE_PRICE.has(m?.price);

export function normalizeMeter(raw = {}, { now = Date.now(), tz = "Europe/London" } = {}) {
  const kind = KINDS.includes(raw.provider_kind) ? raw.provider_kind : null;
  const idOk = raw.model_id === undefined || raw.model_id === null || (typeof raw.model_id === "string" && SAFE_ID.test(raw.model_id));
  let reset = iso(raw.reset_at);
  if (!reset && typeof raw.reset_local === "string" && validZone(raw.tz || tz)) {
    const t = parseLocal(raw.reset_local, raw.tz || tz);
    reset = t === null ? null : new Date(t).toISOString();
  }
  const m = {
    provider_kind: kind, provider: txt(raw.provider, 48), model_id: idOk ? (raw.model_id ?? null) : null,
    task: txt(raw.task, 24),
    verified_free: raw.verified_free === true, price: raw.price === 0 ? 0 : txt(raw.price, 32),
    used: count(raw.used), remaining: count(raw.remaining), limit: count(raw.limit),
    window: parseWindow(raw.window) ? raw.window.trim().toLowerCase() : null,
    reset_at: reset, checked_at: iso(raw.checked_at),
    confidence: ["high", "medium", "manual", "low", "none"].includes(raw.confidence) ? raw.confidence : "none",
    privacy: ["local", "provider-retains", "unknown"].includes(raw.privacy) ? raw.privacy : "unknown",
    eligibility: "unknown", unknown_reason: null, evidence_url: /^https:\/\/[^\s]{1,200}$/.test(raw.evidence_url || "") ? raw.evidence_url : null,
  };
  const why = !kind ? ["blocked", "unknown provider_kind"]
    : !idOk ? ["blocked", "unsafe model_id rejected"]
    : !m.provider ? ["blocked", "missing provider"]
    : !zeroSpend(m) ? ["blocked", "not free or prepaid: price " + m.price]
    : kind === "API" && !m.verified_free ? ["unknown", txt(raw.unknown_reason) || "free entitlement unverified"]
    : null;
  if (why) { m.eligibility = why[0]; m.unknown_reason = why[1]; return m; }
  if (kind !== "LOCAL") {
    const age = m.checked_at ? now - Date.parse(m.checked_at) : null;
    const resetPassed = m.reset_at && Date.parse(m.reset_at) <= now; // counters from before a reset are superseded
    if (age === null) return { ...m, unknown_reason: "no checked_at" };
    if (age < -300_000) return { ...m, unknown_reason: "checked_at in the future" };
    if (age > MAX_AGE[kind] && !resetPassed) return { ...m, unknown_reason: "stale counters" };
    const bad = (m.remaining !== null && m.limit !== null && m.remaining > m.limit) ||
      (m.used !== null && m.remaining !== null && m.limit !== null && m.used + m.remaining !== m.limit);
    if (bad) return { ...m, confidence: "low", unknown_reason: "counters inconsistent" };
  }
  m.eligibility = "eligible";
  return m;
}

// Same provider/model/window from two sources that disagree: keep the lower remaining, mark low confidence.
export function reconcile(meters) {
  const by = new Map();
  for (const m of meters) {
    const k = [m.provider, m.model_id, m.window].join("|");
    const o = by.get(k);
    if (!o) { by.set(k, m); continue; }
    if (o.remaining !== m.remaining || o.limit !== m.limit) {
      const lo = (o.remaining ?? Infinity) <= (m.remaining ?? Infinity) ? o : m;
      by.set(k, { ...lo, confidence: "low", eligibility: lo.eligibility === "eligible" ? "unknown" : lo.eligibility,
        unknown_reason: lo.unknown_reason || "sources disagree" });
    } else if (m.eligibility === "eligible" && o.eligibility !== "eligible") by.set(k, o); // keep the cautious one
  }
  return [...by.values()];
}

// Editable manual snapshot: { tz?, meters:[{provider, provider_kind:"APP", window, remaining, limit, reset_at|reset_local, checked_at}] }
// APP rows default to price "subscription-prepaid" and confidence "manual"; the file is user-edited, never scraped.
export async function loadSnapshot(file, opts = {}) {
  const snap = JSON.parse(await readFile(file, "utf8"));
  const tz = validZone(snap.tz) ? snap.tz : (opts.tz || "Europe/London");
  const rows = Array.isArray(snap.meters) ? snap.meters.slice(0, 200) : [];
  return rows.map(r => normalizeMeter({ price: "subscription-prepaid", confidence: "manual", privacy: "provider-retains", provider_kind: "APP", ...r }, { ...opts, tz }));
}

// usage.mjs statuses -> meters.
export function metersFromUsage(status, { now = Date.now() } = {}) {
  const p = status.provider, at = status.checked_at || null;
  const needsKey = status.reason === "API key env var not set";
  const base = { provider: p, provider_kind: p === "ollama" ? "LOCAL" : "API", checked_at: at, evidence_url: status.quota_source?.startsWith("https://") ? status.quota_source : null };
  if (p === "ollama") return [normalizeMeter({ ...base, price: "local", verified_free: true, privacy: "local", confidence: "medium", checked_at: new Date(now).toISOString() }, { now })];
  if (needsKey || status.configured === false) return [normalizeMeter({ ...base, price: "free", unknown_reason: "account missing: API key env var not set" }, { now })];
  const q = status.free_model_daily_requests;
  if (q) return [normalizeMeter({ ...base, window: "daily", price: "free", verified_free: !!q.verified, used: q.used, remaining: q.remaining, limit: q.limit, confidence: q.verified ? "high" : "none", unknown_reason: q.verified ? null : "quota unverified" }, { now })];
  if (status.requests) return ["requests", "tokens"].map(k => {
    const r = status[k], ms = parseDuration(r.resets_in), t = Date.parse(at);
    return normalizeMeter({ ...base, task: k, window: r.window, price: "free", verified_free: false, remaining: r.remaining, limit: r.limit,
      reset_at: ms !== null && !Number.isNaN(t) ? new Date(t + ms).toISOString() : null, confidence: r.verified ? "medium" : "none",
      unknown_reason: "free entitlement unverified; counters are observed headers only" }, { now });
  });
  return [normalizeMeter({ ...base, price: "free", unknown_reason: status.reason || "quota unavailable" }, { now })];
}
