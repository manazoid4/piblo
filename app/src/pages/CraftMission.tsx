import { useEffect, useState } from 'react';
import { agentTone, groupByAgent, nextAction, STATUS_LABEL, type MissionData, type MissionTask } from '../lib/mission';

const isUrl = (s: string) => /^https?:\/\//.test(s);
const tone = { done: 'ok', running: 'warn', failed: 'bad', waiting: '' } as const;

function Proof({ proof }: { proof: string }) {
  return isUrl(proof)
    ? <a href={proof} onClick={(e) => { e.preventDefault(); void window.piblo.openExternal(proof); }}>Proof</a>
    : <span className="meta" title={proof} style={{ overflowWrap: 'anywhere' }}>Proof: {proof}</span>;
}

function TaskRow({ t }: { t: MissionTask }) {
  return <li className="row" style={{ alignItems: 'baseline', flexWrap: 'wrap' }}>
    <span className={`pill ${tone[t.status]}`}>{STATUS_LABEL[t.status]}</span>
    <span className="grow" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{t.title}{t.detail && <span className="meta"> · {t.detail}</span>}</span>
    {t.proof && <Proof proof={t.proof} />}
  </li>;
}

export function CraftMission() {
  const [data, setData] = useState<MissionData | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => void window.piblo.craftMission!.get().then((d) => live && setData(d)).catch(() => live && setData(null));
    load();
    const timer = setInterval(load, 10_000);
    return () => { live = false; clearInterval(timer); };
  }, []);

  return <section className="card stack" aria-labelledby="craft-mission">
    <h2 id="craft-mission">Mission control</h2>
    {!data ? <p className="meta">Core is not reachable right now.</p> : <>
      <p><strong>Next:</strong> {nextAction(data.tasks)}</p>
      {data.error === 'cswap_unavailable' && <p className="meta">Agent usage is unavailable (cswap not found).</p>}
      <div className="row" style={{ flexWrap: 'wrap' }} aria-label="Agents">
        {data.agents.map((a) => {
          const k = agentTone(a);
          return <span key={a.id} className={`pill ${k === 'ok' || k === 'warn' ? k : ''}`} style={a.installed ? undefined : { opacity: 0.55 }}>
            <span className="dot" />{a.label} · {a.installed ? (a.pct_left === null ? '—' : `${a.pct_left}%`) : 'not installed'}
          </span>;
        })}
      </div>
      <div className="stack">
        {groupByAgent(data.agents, data.tasks).filter((g) => g.tasks.length).map((g) => <div key={g.id} className="stack">
          <h3 className="meta">{g.agent?.label ?? g.id}</h3>
          <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>{g.tasks.map((t) => <TaskRow key={t.id} t={t} />)}</ul>
        </div>)}
        {!data.tasks.length && <p className="meta">No tasks yet.</p>}
      </div>
    </>}
  </section>;
}
