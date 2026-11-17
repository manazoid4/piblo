/** Token usage from local Claude/Codex logs plus OpenRouter credits. Block logic follows ccusage (MIT); no code copied. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { DayTotal, UsageSnapshot } from '../shared/types';

const HOUR = 3600_000;
const BLOCK = 5 * HOUR;
type Entry = { ts: number; tokens: number; key: string | null };

export function parseClaudeLines(text: string): Entry[] {
  const out: Entry[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try {
      const d = JSON.parse(line);
      const u = d.message?.usage;
      const ts = new Date(d.timestamp).getTime();
      if (!u || isNaN(ts)) continue;
      const tokens = (u.input_tokens || 0) + (u.output_tokens || 0) + (u.cache_creation_input_tokens || 0) + (u.cache_read_input_tokens || 0);
      out.push({ ts, tokens, key: d.message?.id && d.requestId ? `${d.message.id}:${d.requestId}` : null });
    } catch { /* skip malformed line */ }
  }
  return out;
}

/** Tokens in the 5-hour block that contains nowMs (blocks start at the hour of their first entry), else null. */
export function claudeBlock(entries: { ts: number; tokens: number }[], nowMs: number): { tokens: number; start: string; end: string } | null {
  let cur: { tokens: number; start: number; end: number; last: number } | null = null;
  for (const e of [...entries].sort((a, b) => a.ts - b.ts)) {
    if (!cur || e.ts >= cur.end || e.ts - cur.last >= BLOCK) {
      const start = Math.floor(e.ts / HOUR) * HOUR;
      cur = { tokens: 0, start, end: start + BLOCK, last: e.ts };
    }
    cur.tokens += e.tokens;
    cur.last = e.ts;
  }
  if (!cur || nowMs < cur.start || nowMs >= cur.end) return null;
  return { tokens: cur.tokens, start: new Date(cur.start).toISOString(), end: new Date(cur.end).toISOString() };
}

const dayKey = (ms: number) => {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/** Last `days` local days, oldest first, zero-filled. */
export function dailyTotals(entries: { ts: number; tokens: number }[], nowMs: number, days = 7): DayTotal[] {
  const m = new Map<string, number>();
  for (const e of entries) m.set(dayKey(e.ts), (m.get(dayKey(e.ts)) ?? 0) + e.tokens);
  return Array.from({ length: days }, (_, i) => {
    const day = dayKey(nowMs - (days - 1 - i) * 24 * HOUR);
    return { day, tokens: m.get(day) ?? 0 };
  });
}

/** Codex logs hold a running total; the last token_count line is the session's total. */
export function parseCodexSession(text: string): { ts: number; tokens: number } | null {
  let last: { ts: number; tokens: number } | null = null;
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try {
      const d = JSON.parse(line);
      const total = d.payload?.info?.total_token_usage?.total_tokens;
      const ts = new Date(d.timestamp).getTime();
      if (d.payload?.type === 'token_count' && typeof total === 'number' && !isNaN(ts)) last = { ts, tokens: total };
    } catch { /* skip malformed line */ }
  }
  return last;
}

export interface UsageDeps {
  /** .jsonl files under dir modified at or after sinceMs, or null if dir is missing. */
  listJsonl(dir: string, sinceMs: number): string[] | null;
  readFile(p: string): string | null;
  home: string;
  env: Record<string, string | undefined>;
  fetch: typeof fetch;
  /** Key saved in the app; the OPENROUTER_API_KEY env var wins. */
  savedKey?: string;
}

export async function scanUsage(deps: UsageDeps, nowMs: number): Promise<UsageSnapshot> {
  const since = nowMs - 8 * 24 * HOUR;
  const snap: UsageSnapshot = {
    generatedAt: new Date(nowMs).toISOString(),
    claude: { status: 'not-connected', fiveHour: null, week: { tokens: 0 }, daily: [] },
    codex: { status: 'not-connected', today: 0, week: 0, daily: [] },
    openrouter: { status: 'not-connected' },
  };

  const roots = (deps.env.CLAUDE_CONFIG_DIR || path.join(deps.home, '.claude')).split(',').map((r) => path.join(r.trim(), 'projects'));
  const seen = new Set<string>();
  const claude: Entry[] = [];
  let found = false;
  for (const root of roots) {
    const files = deps.listJsonl(root, since);
    if (!files) continue;
    found = true;
    for (const f of files) {
      for (const e of parseClaudeLines(deps.readFile(f) ?? '')) {
        if (e.key) {
          if (seen.has(e.key)) continue;
          seen.add(e.key);
        }
        claude.push(e);
      }
    }
  }
  if (found) {
    snap.claude.status = 'ok';
    snap.claude.fiveHour = claudeBlock(claude, nowMs);
    snap.claude.daily = dailyTotals(claude, nowMs);
    snap.claude.week.tokens = snap.claude.daily.reduce((a, d) => a + d.tokens, 0);
  }

  const cfiles = deps.listJsonl(path.join(deps.home, '.codex', 'sessions'), since);
  if (cfiles) {
    const totals = cfiles.flatMap((f) => parseCodexSession(deps.readFile(f) ?? '') ?? []);
    snap.codex.status = 'ok';
    snap.codex.daily = dailyTotals(totals, nowMs);
    snap.codex.today = snap.codex.daily[6].tokens;
    snap.codex.week = snap.codex.daily.reduce((a, d) => a + d.tokens, 0);
  }

  const key = (deps.env.OPENROUTER_API_KEY || deps.savedKey || '').trim();
  if (key) {
    try {
      const res = await deps.fetch('https://openrouter.ai/api/v1/credits', { headers: { Authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(8000) });
      if (res.ok) {
        const d = (await res.json()) as { data?: { total_credits?: number; total_usage?: number } };
        snap.openrouter = { status: 'ok', totalCredits: d.data?.total_credits, totalUsage: d.data?.total_usage };
      } else {
        snap.openrouter = { status: 'error', message: res.status === 401 ? 'OpenRouter rejected the key.' : `OpenRouter answered ${res.status}.` };
      }
    } catch {
      snap.openrouter = { status: 'error', message: 'Could not reach OpenRouter.' }; // fixed text: never echo error text that may contain the key
    }
  }
  return snap;
}

function walk(dir: string, sinceMs: number, out: string[]) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, sinceMs, out);
    else if (e.name.endsWith('.jsonl') && fs.statSync(p).mtimeMs >= sinceMs) out.push(p);
  }
}

export const realUsageDeps = (savedKey?: string): UsageDeps => ({
  listJsonl(dir, sinceMs) {
    if (!fs.existsSync(dir)) return null;
    const out: string[] = [];
    try { walk(dir, sinceMs, out); } catch { /* unreadable subfolder: use what we have */ }
    return out;
  },
  readFile: (p) => { try { return fs.readFileSync(p, 'utf8'); } catch { return null; } },
  home: os.homedir(),
  env: process.env,
  fetch,
  savedKey,
});
