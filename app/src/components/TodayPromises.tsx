import { useState } from 'react';
import { usePoll, useAct, useStore } from '../lib/store';
import { Icon, Empty, Spinner } from './ui';

interface PromiseCard {
  id: string;
  state: 'due' | 'waiting' | 'done';
  title: string;
  note: string;
  when: string;
  due_label: string;
  overdue: boolean;
  actions: string[];
}

interface TodayPromisesResponse {
  ok: boolean;
  cards: PromiseCard[];
  empty: boolean;
  counts: { due: number; waiting: number; done: number };
}

interface ProposeResponse {
  ok: boolean;
  note: string;
  candidates: { due: string; label: string }[];
  ambiguous: boolean;
  default_time: boolean;
  confirm_required: boolean;
}

interface ConfirmResponse {
  ok: boolean;
  id: string;
  due_label: string;
  due_epoch: number;
  duplicate: boolean;
}

export function TodayPromises() {
  const { core, toast } = useStore();
  const running = core?.phase === 'running';
  const today = usePoll<TodayPromisesResponse>('/promises/today', running ? 30000 : 0);
  const act = useAct();

  const [proposing, setProposing] = useState(false);
  const [note, setNote] = useState('');
  const [proposeData, setProposeData] = useState<ProposeResponse | null>(null);
  const [proposeLoading, setProposeLoading] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);

  const handlePropose = async () => {
    if (!note.trim()) return;
    setProposeLoading(true);
    setProposeData(null);
    const r = await window.piblo.api<ProposeResponse>('POST', '/promises/propose', { note: note.trim() });
    setProposeLoading(false);
    if (r.ok) {
      setProposeData(r.data);
    } else {
      toast(r.message, true);
    }
  };

  const handleConfirm = async (due: string, label: string) => {
    setConfirming(due);
    const r = await window.piblo.api<ConfirmResponse>('POST', '/promises/confirm', {
      note: note.trim(),
      due,
      confirmed: true,
    });
    setConfirming(null);
    if (r.ok) {
      toast(r.data.duplicate ? 'Promise already set' : `Promise set for ${label}`);
      setNote('');
      setProposeData(null);
      today.reload();
    } else {
      toast(r.message, true);
    }
  };

  const handleAction = async (card: PromiseCard, action: 'done' | 'later' | 'undo') => {
    let path: string;
    let body: unknown;
    if (action === 'done') {
      path = `/promises/${card.id}/done`;
    } else if (action === 'later') {
      path = `/promises/${card.id}/later`;
      body = { minutes: 60 };
    } else {
      path = `/promises/${card.id}/undo`;
    }
    const r = await act('POST', path, body, undefined);
    if (r.ok) today.reload();
  };

  if (!running) {
    return (
      <Empty title="Core not running">
        <p>Start Piblo Core to see your promises.</p>
      </Empty>
    );
  }

  if (today.loading) {
    return <div className="card"><Spinner /></div>;
  }

  if (today.error) {
    return <Empty title="Could not load promises"><p>{today.error}</p></Empty>;
  }

  const cards = today.data?.cards ?? [];
  const counts = today.data?.counts ?? { due: 0, waiting: 0, done: 0 };

  return (
    <div className="card stack">
      <div className="row">
        <h2>Today</h2>
        <span className="meta">Due {counts.due} · Waiting {counts.waiting} · Done {counts.done}</span>
      </div>

      {!proposing && (
        <button className="btn primary" onClick={() => setProposing(true)}>
          <Icon name="plug" size={16} /> New promise
        </button>
      )}

      {proposing && (
        <div className="stack">
          <input
            className="input"
            placeholder="Type a promise (e.g. 'Call mum by Friday 5pm')"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && void handlePropose()}
            maxLength={140}
            autoFocus
            aria-label="Promise note"
          />
          {proposeLoading && <Spinner />}
          {proposeData && (
            <div className="stack">
              <p className="sub">Choose the time:</p>
              {proposeData.candidates.map((c, i) => (
                <button
                  key={i}
                  className="btn"
                  onClick={() => handleConfirm(c.due, c.label)}
                  disabled={confirming === c.due}
                >
                  {confirming === c.due ? <Spinner /> : c.label}
                </button>
              ))}
              {proposeData.ambiguous && (
                <p className="sub meta">Multiple readings found. Pick one.</p>
              )}
            </div>
          )}
          {!proposeLoading && !proposeData && note.trim() && (
            <button className="btn primary" onClick={handlePropose} disabled={!note.trim()}>
              Propose times
            </button>
          )}
          <button className="btn ghost" onClick={() => { setProposing(false); setNote(''); setProposeData(null); }}>
            Cancel
          </button>
        </div>
      )}

      {cards.length === 0 && !proposing && (
        <Empty title="No promises today">
          <p>Add one above or say "I'll do X by Friday" to Piblo.</p>
        </Empty>
      )}

      <div className="list">
        {cards.map((card) => (
          <div key={card.id} className={`list-item ${card.state} ${card.overdue ? 'overdue' : ''}`}>
            <div className="grow">
              <div className="row">
                <span className={`tag ${card.state === 'due' ? 'warn' : card.state === 'done' ? 'ok' : ''}`}>
                  {card.state === 'due' && card.overdue && 'OVERDUE'}
                  {card.state === 'due' && !card.overdue && 'DUE'}
                  {card.state === 'waiting' && 'WAITING'}
                  {card.state === 'done' && 'DONE'}
                </span>
                <span className="meta">{card.when}</span>
              </div>
              <div className="detail selectable">{card.title}</div>
            </div>
            <div className="row wrap" style={{ gap: 'var(--s-2)' }}>
              {card.actions.includes('done') && (
                <button className="btn sm primary" onClick={() => handleAction(card, 'done')}>Done</button>
              )}
              {card.actions.includes('later') && (
                <button className="btn sm" onClick={() => handleAction(card, 'later')}>+1h</button>
              )}
              {card.actions.includes('undo') && (
                <button className="btn sm danger" onClick={() => handleAction(card, 'undo')}>Undo</button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}