import { useEffect, useState } from 'react';
import { agentTone, groupByAgent, missionProof, nextAction, STATUS_LABEL, type MissionData, type MissionTask } from '../lib/mission';
import './CraftMission.css';

const tone = { done: 'ok', running: 'warn', failed: 'bad', waiting: '' } as const;

function TaskRow({ t }: { t: MissionTask }) {
  const proof = missionProof(t.proof);
  const href = proof?.href;
  const proved = t.status === 'done' && proof !== null;
  const progress = t.progress;
  return <li className="craft-mission-task">
    <div className="row craft-mission-task-head">
      <span className={`pill ${proved ? 'craft-mission-proved' : tone[t.status]}`}>{proved ? 'Proved' : STATUS_LABEL[t.status]}</span>
      <strong className="grow">{t.title}</strong>
      {typeof progress === 'number' && progress >= 0 && progress <= 100 && <span className="meta" aria-label={`Progress ${progress}%`}>{progress}%</span>}
    </div>
    {t.detail && <p className="meta">{t.detail}</p>}
    {proof && <p className="meta">Proof: {href
      ? <a href={href} onClick={(e) => { e.preventDefault(); void window.piblo.openExternal(href); }}>{proof.text}</a>
      : proof.text}</p>}
  </li>;
}

export function CraftMission() {
  const [data, setData] = useState<MissionData | null>();
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const snapshot = await window.piblo.craftMission!.get();
        if (live) setData(snapshot);
      } catch {
        if (live) setData(null);
      } finally {
        if (live) timer = setTimeout(load, 10_000);
      }
    }
    void load();
    return () => { live = false; clearTimeout(timer); };
  }, []);

  return <section className="card stack" aria-labelledby="craft-mission">
    <h2 id="craft-mission">Mission control</h2>
    {!data ? <p className="meta">{data === undefined ? 'Loading tasks…' : 'Core is not reachable right now.'}</p> : <>
      <p><strong>Next:</strong> {nextAction(data.tasks)}</p>
      {data.error === 'cswap_unavailable' && <p className="meta">Claude usage is unavailable.</p>}
      <div className="row" style={{ flexWrap: 'wrap' }} aria-label="Agents">
        {data.agents.map((a) => {
          const k = agentTone(a);
          return <span key={a.id} className={`pill ${k === 'ok' || k === 'warn' ? k : ''}`} style={a.installed ? undefined : { opacity: 0.55 }}>
            <span className="dot" />{a.label} · {a.installed ? (a.pct_left === null ? 'Usage unavailable' : `${a.pct_left}% left`) : 'not installed'}
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
