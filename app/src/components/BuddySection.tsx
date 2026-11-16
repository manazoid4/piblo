import { Setting, Switch } from './ui';
import { useAct, usePoll } from '../lib/store';

export type BodyDouble = { enabled: boolean; mode: 'glance' | 'voice' | 'silent'; interval_min: number; break_every: number };

export const BODY_DOUBLE_MODES = [
  ['glance', 'Voice and window'],
  ['voice', 'Voice only'],
  ['silent', 'Silent'],
] as const;

/** One plain line saying what the chosen mode does. */
export function bodyDoubleLine(s: BodyDouble): string {
  if (!s.enabled) return 'Off.';
  const every = `every ${s.interval_min} minute${s.interval_min === 1 ? '' : 's'}`;
  if (s.mode === 'glance') return `Piblo asks ${every} and names the window you are in. The title is read on this PC and never sent online.`;
  if (s.mode === 'voice') return `Piblo asks ${every} if you are still on track.`;
  return `No questions. A quiet buzz every ${s.break_every * s.interval_min} minutes for a break.`;
}

/** Body-double check-ins: how often Piblo looks in and how. */
export function BuddySection() {
  const act = useAct();
  const settings = usePoll<BodyDouble>('/bodydouble', 0);
  const s = settings.data;
  const save = async (p: Partial<BodyDouble>) => {
    const r = await act<void>('PATCH', '/bodydouble', p, 'Check-ins updated');
    if (r.ok) settings.reload();
  };
  return (
    <section className="card" aria-labelledby="buddy-h">
      <h2 id="buddy-h">Check-ins</h2>
      <Setting title="Body-double check-ins" sub={s ? bodyDoubleLine(s) : 'Loading...'}>
        <Switch label="Check-ins" checked={s?.enabled ?? false} disabled={!s} onChange={(v) => void save({ enabled: v })} />
      </Setting>
      <Setting title="How Piblo checks in">
        <div className="seg" role="group" aria-label="Check-in mode">
          {BODY_DOUBLE_MODES.map(([v, l]) => (
            <button key={v} aria-pressed={s?.mode === v} disabled={!s} onClick={() => void save({ mode: v })}>{l}</button>
          ))}
        </div>
      </Setting>
      <Setting title="Minutes between check-ins">
        <div className="seg" role="group" aria-label="Interval">
          {[15, 25, 45].map((m) => (
            <button key={m} aria-pressed={s?.interval_min === m} disabled={!s} onClick={() => void save({ interval_min: m })}>{m}</button>
          ))}
        </div>
      </Setting>
    </section>
  );
}
