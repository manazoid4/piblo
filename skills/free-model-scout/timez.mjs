// Timezone-safe helpers (stdlib Intl only). Local wall-clock arithmetic keeps DST correct.
const fmtCache = new Map();
function dtf(tz) {
  if (!fmtCache.has(tz)) fmtCache.set(tz, new Intl.DateTimeFormat("en-GB", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", second: "numeric" }));
  return fmtCache.get(tz);
}
export function localParts(ms, tz) {
  const p = Object.fromEntries(dtf(tz).formatToParts(new Date(ms)).map(x => [x.type, Number(x.value)]));
  return { y: p.year, m: p.month, d: p.day, h: p.hour, mi: p.minute, s: p.second };
}
const offsetMs = (ms, tz) => { const p = localParts(ms, tz); return Date.UTC(p.y, p.m - 1, p.d, p.h, p.mi, p.s) - Math.floor(ms / 1000) * 1000; };
// Local wall-clock -> instant. Gap times (spring forward) shift forward; ambiguous times (autumn) pick one.
export function localToInstant(y, m, d, h, mi, s, tz) {
  const guess = Date.UTC(y, m - 1, d, h, mi, s);
  return guess - offsetMs(guess - offsetMs(guess, tz), tz);
}
export const validZone = (tz) => { try { dtf(tz); return typeof tz === "string"; } catch { return false; } };
export function addLocalDays(ms, n, tz) {
  const p = localParts(ms, tz);
  return localToInstant(p.y, p.m, p.d + n, p.h, p.mi, p.s, tz);
}
// "2026-11-15T23:00[:30]" (no offset) interpreted in tz.
export function parseLocal(str, tz) {
  const m = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)(?::(\d\d))?$/.exec(str || "");
  return m ? localToInstant(+m[1], +m[2], +m[3], +m[4], +m[5], +(m[6] || 0), tz) : null;
}
export function fmtLocal(ms, tz) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", day: "2-digit", month: "short",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "short" }).format(new Date(ms));
}
// Window spec -> { ms } for fixed lengths or { days } for calendar windows (wall-clock preserved across DST).
export function parseWindow(w) {
  if (typeof w !== "string") return null;
  const s = w.trim().toLowerCase();
  if (s === "daily") return { days: 1 };
  if (s === "weekly") return { days: 7 };
  if (s === "per-minute") return { ms: 60_000 };
  const m = /^(\d{1,3})(m|h|d)$/.exec(s);
  if (!m || +m[1] < 1) return null;
  return m[2] === "d" ? { days: +m[1] } : { ms: +m[1] * (m[2] === "h" ? 3_600_000 : 60_000) };
}
// Next reset strictly after `now`, stepping a known reset_at by the window. Null if impossible.
export function nextReset(resetMs, win, now, tz) {
  if (resetMs === null || resetMs === undefined) return null;
  if (resetMs > now) return resetMs;
  if (!win) return null;
  let t = resetMs;
  for (let i = 0; i < 5000 && t <= now; i++) t = win.days ? addLocalDays(t, win.days, tz) : t + win.ms;
  return t > now ? t : null;
}
// Groq-style "1h2m3.5s" / "7.66s" / "4h" -> ms.
export function parseDuration(str) {
  if (typeof str !== "string" || !/^(\d+(\.\d+)?(d|h|ms|m|s))+$/.test(str)) return null;
  let total = 0;
  for (const [, n, u] of str.matchAll(/(\d+(?:\.\d+)?)(d|h|ms|m|s)/g))
    total += Number(n) * ({ d: 864e5, h: 36e5, m: 6e4, s: 1e3, ms: 1 })[u];
  return Math.round(total);
}
