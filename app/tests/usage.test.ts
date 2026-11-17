import { describe, it, expect } from 'vitest';
import { claudeBlock, dailyTotals, parseClaudeLines, parseCodexSession, scanUsage, type UsageDeps } from '../electron/usage';

const H = 3600_000;
const line = (ts: string, id: string, req: string | null, u: object) => JSON.stringify({ timestamp: ts, requestId: req ?? undefined, message: { id, usage: u } });

describe('usage parsing', () => {
  it('sums the four token fields, skips junk, dedupes message:request', () => {
    const text = [
      'not json',
      line('2026-03-30T10:00:00Z', 'm1', 'r1', { input_tokens: 10, output_tokens: 5 }),
      line('2026-03-30T10:00:01Z', 'm1', 'r1', { input_tokens: 10, output_tokens: 5 }),
      line('2026-03-30T10:03:00Z', 'm2', null, { cache_creation_input_tokens: 2, cache_read_input_tokens: 3 }),
      '{"timestamp":"2026-03-30T10:04:00Z","type":"user"}',
    ].join('\n');
    const e = parseClaudeLines(text);
    expect(e.map((x) => x.tokens)).toEqual([15, 15, 5]); // dedupe happens across files in scanUsage
  });

  it('claudeBlock: 5h blocks start on the hour, gap starts a new one, null when none active', () => {
    const base = Date.parse('2026-03-30T10:20:00Z');
    const entries = [{ ts: base, tokens: 100 }, { ts: base + H, tokens: 200 }, { ts: base + 6 * H, tokens: 500 }];
    expect(claudeBlock(entries.slice(0, 2), base + 1800_000 * 3)).toMatchObject({ tokens: 300, start: '2026-03-30T10:00:00.000Z', end: '2026-03-30T15:00:00.000Z' });
    expect(claudeBlock(entries, base + 6 * H + 1000)).toMatchObject({ tokens: 500, start: '2026-03-30T16:00:00.000Z' });
    expect(claudeBlock(entries, base + 12 * H)).toBeNull();
    expect(claudeBlock([], base)).toBeNull();
  });

  it('dailyTotals gives 7 zero-filled days, oldest first', () => {
    const now = Date.parse('2026-03-30T12:00:00Z');
    const d = dailyTotals([{ ts: now, tokens: 100 }, { ts: now - 24 * H, tokens: 250 }], now);
    expect(d).toHaveLength(7);
    expect([d[0].tokens, d[5].tokens, d[6].tokens]).toEqual([0, 250, 100]);
  });

  it('parseCodexSession takes the last running total', () => {
    expect(parseCodexSession('{"x":1}')).toBeNull();
    const t = (ts: string, n: number) => JSON.stringify({ timestamp: ts, payload: { type: 'token_count', info: { total_token_usage: { total_tokens: n } } } });
    expect(parseCodexSession([t('2026-03-30T10:00:00Z', 100), t('2026-03-30T10:05:00Z', 350)].join('\n'))?.tokens).toBe(350);
  });
});

describe('scanUsage', () => {
  const now = Date.parse('2026-03-30T12:00:00Z');
  const base = (over: Partial<UsageDeps> = {}): UsageDeps => ({
    listJsonl: () => null, readFile: () => null, home: '/h', env: {}, fetch: (async () => ({ ok: true, json: async () => ({}) })) as never, ...over,
  });

  it('reports not-connected when nothing exists', async () => {
    const s = await scanUsage(base(), now);
    expect([s.claude.status, s.codex.status, s.openrouter.status]).toEqual(['not-connected', 'not-connected', 'not-connected']);
  });

  it('reads claude logs across files with dedupe, and an active block', async () => {
    const l = line('2026-03-30T11:00:00Z', 'm1', 'r1', { input_tokens: 40 });
    const s = await scanUsage(base({ listJsonl: (d) => (d.replace(/\\/g, '/').endsWith('.claude/projects') ? ['a', 'b'] : null), readFile: () => l }), now);
    expect(s.claude.status).toBe('ok');
    expect(s.claude.fiveHour?.tokens).toBe(40);
    expect(s.claude.week.tokens).toBe(40);
    expect(s.codex.status).toBe('not-connected');
  });

  it('openrouter: ok, http error, and the key never appears in the error text', async () => {
    const key = 'sk-or-secret-12345';
    const ok = await scanUsage(base({ env: { OPENROUTER_API_KEY: key }, fetch: (async () => ({ ok: true, json: async () => ({ data: { total_credits: 50, total_usage: 12.5 } }) })) as never }), now);
    expect(ok.openrouter).toMatchObject({ status: 'ok', totalCredits: 50, totalUsage: 12.5 });
    const bad = await scanUsage(base({ savedKey: key, fetch: (async () => { throw new Error(`boom ${key}`); }) as never }), now);
    expect(bad.openrouter.status).toBe('error');
    expect(JSON.stringify(bad)).not.toContain(key);
    const http = await scanUsage(base({ savedKey: key, fetch: (async () => ({ ok: false, status: 401 })) as never }), now);
    expect(http.openrouter.message).toMatch(/rejected/);
  });
});
