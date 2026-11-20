import { useCallback, useEffect, useState } from 'react';
import type { DayTotal, UsageSnapshot } from '../../shared/types';
import { PageHead } from '../components/ui';

const fmt = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n));
const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

function Bars({ days }: { days: DayTotal[] }) {
  const max = Math.max(1, ...days.map((d) => d.tokens));
  return (
    <div className="stack" role="list" aria-label="Tokens per day, last 7 days">
      {days.map((d) => (
        <div className="row" role="listitem" key={d.day}>
          <span className="meta" style={{ width: 84 }}>{d.day.slice(5)}</span>
          <div className="grow" style={{ background: 'var(--border, rgba(127,127,127,.2))', borderRadius: 4, height: 10 }}>
            <div style={{ width: `${(d.tokens / max) * 100}%`, height: '100%', borderRadius: 4, background: 'var(--accent, currentColor)' }} />
          </div>
          <span className="meta" style={{ width: 64, textAlign: 'right' }}>{fmt(d.tokens)}</span>
        </div>
      ))}
    </div>
  );
}

export function Usage() {
  const [snap, setSnap] = useState<UsageSnapshot | null>(null);
  const [key, setKey] = useState('');
  const load = useCallback(() => void window.piblo.usage.get().then(setSnap).catch(() => undefined), []);
  useEffect(() => {
    load();
    const t = window.setInterval(load, 60_000);
    return () => window.clearInterval(t);
  }, [load]);

  const saveKey = async () => {
    await window.piblo.usage.setKey(key);
    setKey('');
    load();
  };

  if (!snap) return <><PageHead title="Usage" sub="Loading..." /></>;
  const { claude, codex, openrouter } = snap;
  const merged = claude.daily.map((d, i) => ({ day: d.day, tokens: d.tokens + (codex.daily[i]?.tokens ?? 0) }));

  return (
    <>
      <PageHead title="Usage" sub="Tokens used on this PC. Refreshes every minute." />
      <div className="stack">
        <section className="card stack" aria-labelledby="u-claude">
          <h2 id="u-claude">Claude</h2>
          {claude.status === 'ok' ? (
            <>
              <div className="row"><span className="grow">Current 5-hour block</span>
                <span className="tag">{claude.fiveHour ? `${fmt(claude.fiveHour.tokens)} tokens, ends ${clock(claude.fiveHour.end)}` : 'No active block'}</span></div>
              <div className="row"><span className="grow">Last 7 days</span><span className="tag">{fmt(claude.week.tokens)} tokens</span></div>
            </>
          ) : <p className="meta">Not connected. Use Claude Code on this PC and its logs in ~/.claude/projects show up here.</p>}
        </section>
        <section className="card stack" aria-labelledby="u-codex">
          <h2 id="u-codex">Codex / OpenAI</h2>
          {codex.status === 'ok' ? (
            <>
              <div className="row"><span className="grow">Today</span><span className="tag">{fmt(codex.today)} tokens</span></div>
              <div className="row"><span className="grow">Last 7 days</span><span className="tag">{fmt(codex.week)} tokens</span></div>
            </>
          ) : <p className="meta">Not connected. Run the Codex CLI on this PC and ~/.codex/sessions fills this in.</p>}
        </section>
        <section className="card stack" aria-labelledby="u-or">
          <h2 id="u-or">OpenRouter credits</h2>
          {openrouter.status === 'ok' ? (
            <div className="row"><span className="grow">Remaining</span>
              <span className="tag">${((openrouter.totalCredits ?? 0) - (openrouter.totalUsage ?? 0)).toFixed(2)} of ${(openrouter.totalCredits ?? 0).toFixed(2)}</span></div>
          ) : (
            <>
              <p className="meta">{openrouter.status === 'error' ? openrouter.message : 'Not connected. Set OPENROUTER_API_KEY, or paste a key below.'}</p>
              <div className="row">
                <input className="grow" type="password" autoComplete="off" aria-label="OpenRouter key" value={key} onChange={(e) => setKey(e.target.value)} />
                <button className="btn sm" disabled={!key.trim()} onClick={() => void saveKey()}>Save key</button>
              </div>
            </>
          )}
        </section>
        <section className="card stack" aria-labelledby="u-days">
          <h2 id="u-days">Last 7 days</h2>
          <Bars days={merged} />
          <span className="meta">Claude and Codex combined.</span>
        </section>
      </div>
    </>
  );
}
