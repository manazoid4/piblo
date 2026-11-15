// Zero-spend guard, cooldown state and metadata-only usage ledger.
// Ported in spirit from the owner's mazrouter (router/mazrouter.py): State.cool/limits/record, Router.candidates ordering.
// Deliberately NOT ported: mazrouter's budget knob. Scout has no paid lane, so there is nothing to unlock.
import { readFile, writeFile, appendFile, mkdir, rename, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { SAFE_ID } from "./contract.mjs";
import { parseDuration } from "./timez.mjs";

export const DEFAULT_DIR = process.env.PIBLO_SCOUT_CACHE || join(homedir(), ".cache", "piblo-free-model-scout");
const FREE_STATUS = new Set(["catalog-zero-cost", "local-unmetered"]);
const MAX_COOL_S = 86_400, MAX_LEDGER_READ = 256 * 1024;
export class SpendBlocked extends Error {}

// Throws unless the model is a safe ID with a verified zero-cost (or local) status. Use right before any request.
export function assertZeroSpend(model, { includeUnverified = false } = {}) {
  if (!model || typeof model.id !== "string" || !SAFE_ID.test(model.id)) throw new SpendBlocked("unsafe model id");
  if (!FREE_STATUS.has(model.status) && !(includeUnverified && model.status === "unverified-account-tier")) throw new SpendBlocked("not verified zero-cost: " + model.status);
  return model;
}
const clean = (v, n = 80) => String(v ?? "").replace(/[^\x20-\x7e]/g, " ").slice(0, n);
const num = (v) => (Number.isFinite(Number(v)) && v !== null && v !== "" && Number(v) >= 0 ? Math.round(Number(v)) : null);

// Retry-After seconds | HTTP date | "1m30s" -> seconds (clamped 1s..24h). Missing/garbage -> fallback.
export function retrySeconds(value, now = Date.now(), fallback = 300) {
  let s = null;
  if (typeof value === "string" || typeof value === "number") {
    const v = String(value).trim();
    if (/^\d+(\.\d+)?$/.test(v)) s = Number(v);
    else if (parseDuration(v) !== null) s = parseDuration(v) / 1000;
    else if (/^[A-Za-z]{3}, \d/.test(v) && !Number.isNaN(Date.parse(v))) s = (Date.parse(v) - now) / 1000;
  }
  return Math.min(MAX_COOL_S, Math.max(1, Math.ceil(s ?? fallback)));
}

export class State {
  constructor(dir = DEFAULT_DIR) { this.dir = dir; this.file = join(dir, "state.json"); this.ledger = join(dir, "usage.jsonl"); }
  async read() {
    try {
      const d = JSON.parse(await readFile(this.file, "utf8"));
      return { cooldown: d && typeof d.cooldown === "object" && d.cooldown ? d.cooldown : {}, limits: d && typeof d.limits === "object" && d.limits ? d.limits : {} };
    } catch { return { cooldown: {}, limits: {} }; } // missing/corrupt state = no cooldowns known
  }
  async #write(d) {
    await mkdir(this.dir, { recursive: true, mode: 0o700 });
    const tmp = this.file + "." + process.pid + ".tmp";
    await writeFile(tmp, JSON.stringify(d), { mode: 0o600 });
    await rename(tmp, this.file); // ponytail: last-writer-wins across processes; fine for a single-user CLI
  }
  async cool(key, seconds, reason, now = Date.now()) {
    const d = await this.read();
    d.cooldown[clean(key, 80)] = { until: now + Math.min(MAX_COOL_S, Math.max(1, seconds)) * 1000, reason: clean(reason) };
    await this.#write(d);
  }
  async active(key, now = Date.now()) {
    const e = (await this.read()).cooldown[key];
    return e && e.until > now ? clean(e.reason) || "cooldown" : null;
  }
  async cooling(now = Date.now()) { // key -> until ms, for the planner
    const out = {};
    for (const [k, e] of Object.entries((await this.read()).cooldown)) if (e && e.until > now) out[k] = e.until;
    return out;
  }
  async limits(provider, headers) { // keep only rate-limit headers, short values
    const keep = {};
    for (const [k, v] of Object.entries(headers || {})) if (/ratelimit|^retry-after$/i.test(k)) keep[clean(k, 48).toLowerCase()] = clean(v, 40);
    if (!Object.keys(keep).length) return;
    const d = await this.read();
    d.limits[clean(provider, 48)] = { seen: new Date().toISOString(), ...keep };
    await this.#write(d);
  }
  // Metadata only: never the prompt, output, key or headers. Unsafe model ids are dropped, not stored.
  async record(row) {
    if (!SAFE_ID.test(row?.id || "") || !SAFE_ID.test(row?.provider || "")) return false;
    const line = { at: new Date().toISOString(), provider: row.provider, id: row.id, task: clean(row.task, 24),
      success: row.success === true, status: clean(row.status, 12), latency_ms: num(row.latency_ms), input_tokens: num(row.input_tokens), output_tokens: num(row.output_tokens) };
    await mkdir(this.dir, { recursive: true, mode: 0o700 });
    await appendFile(this.ledger, JSON.stringify(line) + "\n", { mode: 0o600 });
    return true;
  }
  async rows() { // tail of the ledger, bounded
    try {
      const size = (await stat(this.ledger)).size;
      const txt = (await readFile(this.ledger, "utf8")).slice(-MAX_LEDGER_READ);
      return txt.split("\n").slice(size > MAX_LEDGER_READ ? 1 : 0).flatMap(l => { try { return [JSON.parse(l)]; } catch { return []; } });
    } catch { return []; }
  }
}

// 401/402/429: start a cooldown and tell the caller to stop. No retry, no rotation, no fallback.
export async function afterHttp(state, provider, status, headers = {}) {
  if (![401, 402, 429].includes(status)) return { stop: false };
  const h = (n) => headers instanceof Headers ? headers.get(n) : Object.entries(headers).find(([k]) => k.toLowerCase() === n)?.[1];
  const secs = status === 429 ? retrySeconds(h("retry-after")) : MAX_COOL_S;
  await state.cool(provider, secs, "HTTP " + status);
  await state.limits(provider, headers instanceof Headers ? Object.fromEntries(headers) : headers);
  return { stop: true, cooldown_s: secs };
}

// Measured first (observed success, then latency), metadata heuristic after, both labelled. Cooled providers dropped.
export function orderCandidates(models, rows = [], { cooling = {}, now = Date.now() } = {}) {
  const stat = new Map();
  for (const r of rows.slice(-500)) {
    const k = r.provider + "|" + r.id + "|" + r.task;
    const s = stat.get(k) || { n: 0, ok: 0, lat: [] };
    s.n++; if (r.success) { s.ok++; if (r.latency_ms !== null) s.lat.push(r.latency_ms); }
    stat.set(k, s);
  }
  const keyed = models.filter(m => !((cooling[m.provider] ?? 0) > now)).map(m => {
    const s = stat.get(m.provider + "|" + m.id + "|" + m.task);
    const measured = s && s.n >= 2 && s.ok / s.n >= 0.5;
    const lat = s?.lat.length ? s.lat.sort((a, b) => a - b)[s.lat.length >> 1] : Infinity;
    return { ...m, rank_basis: measured ? "measured" : "heuristic", measured: measured ? { runs: s.n, success_rate: +(s.ok / s.n).toFixed(2), median_latency_ms: lat } : null };
  });
  return keyed.sort((a, b) => (a.rank_basis === "measured" ? 0 : 1) - (b.rank_basis === "measured" ? 0 : 1) ||
    (a.measured ? b.measured.success_rate - a.measured.success_rate || a.measured.median_latency_ms - b.measured.median_latency_ms : 0) ||
    b.heuristic_score - a.heuristic_score || a.id.localeCompare(b.id));
}
