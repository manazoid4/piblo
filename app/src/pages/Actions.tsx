import { useEffect, useState } from 'react';
import { Empty, PageHead } from '../components/ui';
import { ago } from '../lib/logic';
import { useAct, usePoll, useStore } from '../lib/store';

interface Pending {
  id: string;
  tool: string;
  summary?: string;
  project?: string;
  left?: number;
}
interface Need {
  id: string;
  tier: string;
  title: string;
  project?: string;
  age: number;
  detail?: string;
}
interface Today {
  done?: number;
  needs?: number;
  errors?: number;
  approved?: number;
  denied?: number;
  agents?: { source: string; done: number }[];
  last?: { text?: string } | null;
}
interface Halt {
  halted?: boolean;
  until?: string | null;
}
interface Focus {
  state?: string;
  task?: string;
  remaining_s?: number;
  muted?: boolean;
}
interface WorkToday {
  items?: { item_id: string; action: string; reason: string; stage?: string }[];
}

const QUICK: { id: string; label: string; confirm?: string }[] = [
  { id: 'play_pause', label: 'Play / pause' },
  { id: 'previous_track', label: 'Previous' },
  { id: 'next_track', label: 'Next' },
  { id: 'volume_down', label: 'Volume down' },
  { id: 'volume_up', label: 'Volume up' },
  { id: 'mute', label: 'Mute' },
  { id: 'desktop', label: 'Show desktop' },
  { id: 'lock', label: 'Lock PC', confirm: 'Lock this PC now?' },
];

function Quiet() {
  const halt = usePoll<Halt>('/buddy/halt', 5000);
  const act = useAct();
  const on = !!halt.data?.halted;
  const set = async (v: boolean, minutes?: number) => {
    const r = await act('POST', '/buddy/halt', { on: v, minutes }, v ? 'Paused. No new asks until it ends.' : 'Resumed');
    if (r.ok) halt.reload();
  };
  return (
    <section className="card stack" aria-labelledby="quiet-h">
      <div className="row">
        <div className="grow">
          <h2 id="quiet-h">Quiet</h2>
          <p className="sub">{on ? `Paused${halt.data?.until ? ` until ${new Date(halt.data.until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}. New asks are declined.` : 'Pause agent asks for a while. It never stops tools that are already running.'}</p>
        </div>
        {on ? (
          <button className="btn primary" onClick={() => void set(false)}>Resume</button>
        ) : (
          <div className="row">
            {[15, 60].map((m) => <button key={m} className="btn" onClick={() => void set(true, m)}>{m === 60 ? '1 hour' : `${m} min`}</button>)}
          </div>
        )}
      </div>
    </section>
  );
}

function Approvals() {
  const pend = usePoll<{ pending: Pending[] }>('/buddy/pending', 3000);
  const act = useAct();
  const decide = async (id: string, decision: 'allow' | 'deny') => {
    const r = await act('POST', '/buddy/decide', { id, decision }, decision === 'allow' ? 'Allowed' : 'Denied');
    if (r.ok) pend.reload();
  };
  const list = pend.data?.pending ?? [];
  return (
    <section className="card stack" aria-labelledby="appr-h">
      <h2 id="appr-h">Approvals</h2>
      <div className="list">
        {list.map((p) => (
          <div className="list-item" key={p.id}>
            <div className="grow">
              <div className="title">Approve {p.tool}</div>
              <div className="detail selectable">{[p.project, p.summary].filter(Boolean).join(': ')}</div>
            </div>
            {typeof p.left === 'number' && <span className="meta">{p.left}s left</span>}
            <button className="btn sm" onClick={() => void decide(p.id, 'deny')}>Deny</button>
            <button className="btn sm primary" onClick={() => void decide(p.id, 'allow')}>Allow</button>
          </div>
        ))}
        {!pend.loading && !list.length && <Empty title="Nothing waiting"><p>When something needs your permission it shows up here and on your Piblo.</p></Empty>}
      </div>
    </section>
  );
}

function Needs() {
  const needs = usePoll<{ items: Need[]; warnings?: string[] }>('/needs', 10000);
  const { setNeedsCount } = useStore();
  const act = useAct();
  const items = (needs.data?.items ?? []).filter((n) => n.tier !== 'approvals');
  useEffect(() => setNeedsCount(items.length), [items.length, setNeedsCount]);
  const mark = async (id: string, kind: 'done' | 'snooze') => {
    const r = await act('POST', `/needs/${encodeURIComponent(id)}/${kind}`, undefined, kind === 'done' ? 'Done' : 'Snoozed for a day');
    if (r.ok) needs.reload();
  };
  return (
    <section className="card stack" aria-labelledby="needs-h">
      <h2 id="needs-h">Needs you</h2>
      <p className="sub">Open pull requests and owner tasks, oldest first.</p>
      <div className="list">
        {items.map((n) => (
          <div className="list-item" key={n.id}>
            <div className="grow">
              <div className="title selectable">{n.title}</div>
              <div className="detail">{[n.project, n.tier, ago(n.age)].filter(Boolean).join(' / ')}</div>
            </div>
            <button className="btn sm ghost" onClick={() => void mark(n.id, 'snooze')}>Snooze</button>
            <button className="btn sm" onClick={() => void mark(n.id, 'done')}>Done</button>
          </div>
        ))}
        {!needs.loading && !items.length && <Empty title="All clear"><p>Nothing needs you right now.</p></Empty>}
      </div>
      {needs.data?.warnings?.map((w) => <p className="meta" key={w}>{w}</p>)}
    </section>
  );
}

function TodayCard() {
  const today = usePoll<Today>('/buddy/today', 10000);
  const work = usePoll<WorkToday>('/work/today', 30000);
  const t = today.data;
  return (
    <section className="card stack" aria-labelledby="today-h">
      <h2 id="today-h">Today</h2>
      <div className="row wrap">
        {([['Done', t?.done], ['Needed you', t?.needs], ['Approved', t?.approved], ['Denied', t?.denied], ['Errors', t?.errors]] as const).map(([l, v]) => (
          <div key={l} className="grow stack" style={{ gap: 0 }}>
            <span style={{ fontSize: 'var(--text-xl)', fontWeight: 'var(--weight-strong)' }}>{v ?? '-'}</span>
            <span className="meta">{l}</span>
          </div>
        ))}
      </div>
      {t?.last?.text && <p className="detail selectable">Last: {t.last.text}</p>}
      {(work.data?.items ?? []).slice(0, 3).map((i) => (
        <div className="list-item" key={i.item_id}>
          <div className="grow"><div className="title">{i.action}</div><div className="detail">{i.reason}</div></div>
        </div>
      ))}
    </section>
  );
}

export function FocusCard() {
  const focus = usePoll<Focus>('/focus/state', 2000);
  const act = useAct();
  const [task, setTask] = useState('');
  const f = focus.data;
  const running = f?.state === 'running';
  const awaitingDone = f?.state === 'awaiting_done';
  const start = async (minutes: number) => {
    const r = await act('POST', '/focus/start', { minutes, task }, `Focus started for ${minutes} min`);
    if (r.ok) focus.reload();
  };
  const end = async (done: boolean) => {
    const r = await act('POST', '/focus/end', { done }, done ? 'Nice work' : 'Focus stopped');
    if (r.ok) focus.reload();
  };
  return (
    <section className="card stack" aria-labelledby="focus-h">
      <h2 id="focus-h">Focus sprint</h2>
      {running || awaitingDone ? (
        <div className="row">
          <div className="grow">
            <div className="title">{f?.task || 'Focus'}</div>
            <div className="detail">{awaitingDone ? 'Time is up. Did you finish?' : `${Math.floor((f?.remaining_s ?? 0) / 60)} min left`}</div>
          </div>
          <button className="btn" onClick={() => void end(false)}>Stop</button>
          <button className="btn primary" onClick={() => void end(true)}>Finish</button>
        </div>
      ) : (
        <>
          <input className="input" placeholder="What are you focusing on? (optional)" value={task} onChange={(e) => setTask(e.target.value)} maxLength={200} aria-label="Focus task" />
          <div className="row wrap">{[15, 25, 50].map((m) => <button key={m} className="btn" onClick={() => void start(m)}>{m} min</button>)}</div>
        </>
      )}
    </section>
  );
}

function Quick() {
  const act = useAct();
  return (
    <section className="card stack" aria-labelledby="quick-h">
      <h2 id="quick-h">Quick actions</h2>
      <p className="sub">One-tap controls for this PC.</p>
      <div className="row wrap">
        {QUICK.map((q) => (
          <button key={q.id} className="btn" onClick={() => { if (!q.confirm || window.confirm(q.confirm)) void act('POST', '/pc/action', { action: q.id }); }}>{q.label}</button>
        ))}
      </div>
    </section>
  );
}

export function Actions() {
  return (
    <div className="page">
      <PageHead title="Actions" sub="What needs you, and what you can do right now." />
      <Quiet />
      <div className="grid-2">
        <Approvals />
        <Needs />
      </div>
      <TodayCard />
      <div className="grid-2">
        <FocusCard />
        <Quick />
      </div>
    </div>
  );
}
