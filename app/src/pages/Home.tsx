import { useState } from 'react';
import { Empty, Icon, PageHead, Spinner, Switch } from '../components/ui';
import { TodayPromises } from '../components/TodayPromises';
import { ago } from '../lib/logic';
import { useAct, usePoll, useStore } from '../lib/store';

interface Beam {
  messages?: { id: string; text: string; direction: string; created: number }[];
}

/** Home: what Piblo is, whether it is here, its software, and a note you can send to it. */
export function Home() {
  const { deviceInfo, pill, core, toast, refreshDevice, health } = useStore();
  const act = useAct();
  const online = pill.state === 'connected';
  const beam = usePoll<Beam>('/beam/history?limit=6', 6000);
  const [note, setNote] = useState('');
  const [checking, setChecking] = useState(false);
  const dev = deviceInfo?.device;
  const hub = deviceInfo?.hub_fw;
  const running = core?.phase === 'running';

  const send = async () => {
    if (!note.trim()) return;
    const r = await act('POST', '/beam/to-pocket', { text: note.trim() }, 'Sent to your Piblo');
    if (r.ok) {
      setNote('');
      beam.reload();
    }
  };
  const check = async () => {
    setChecking(true);
    const r = await act('POST', '/core/update/check', undefined, 'Checked for updates');
    setChecking(false);
    if (r.ok) refreshDevice();
  };
  const install = async () => {
    if (!window.confirm(`Install ${hub?.version ?? 'the new build'} on your Piblo? It restarts when done. Keep it on Wi-Fi with 30%+ battery or charging.`)) return;
    const r = await act('POST', '/core/cardputer/action', { action: 'update' });
    if (r.ok) toast('Update started. Your Piblo restarts when it finishes.');
  };

  return (
    <div className="page">
      <PageHead
        title="Home"
        sub={online ? 'Your Piblo is connected.' : 'Power on your Piblo and it will show up here.'}
        right={
          <button className="btn" onClick={refreshDevice} disabled={!running}>
            <Icon name="refresh" size={16} /> Check again
          </button>
        }
      />

      <div className="home-top">
        <section className="card stack" aria-labelledby="dev-h">
          <h2 id="dev-h">Your Piblo</h2>
          {online && dev ? (
            <>
              <div className="row">
                <span className="grow">Battery</span>
                <div className={`battery ${typeof dev.battery === 'number' && dev.battery >= 0 && dev.battery < 20 ? 'low' : ''}`}>
                  <i style={{ width: `${Math.max(0, Math.min(100, dev.battery ?? 0))}%` }} />
                </div>
                <span className="meta" style={{ width: 36, textAlign: 'right' }}>{pill.battery !== null ? `${pill.battery}%` : '?'}</span>
              </div>
              <div className="row"><span className="grow">Software</span><span className="tag">{pill.firmware || 'unknown'}</span></div>
              <div className="row"><span className="grow">Showing</span><span className="tag">{dev.app || 'unknown'}</span></div>
            </>
          ) : (
            <Empty title="Not connected">
              <p>{running ? 'Piblo is not answering. Check it is on and on the same Wi-Fi.' : 'Piblo on this PC is not running yet.'}</p>
            </Empty>
          )}
        </section>

        <section className="card stack" aria-labelledby="fw-h">
          <h2 id="fw-h">Software on Piblo</h2>
          <dl className="meta">
            <dt>On your Piblo</dt><dd>{online ? `v${dev?.version ?? '?'}` : 'not connected'}</dd>
            <dt>Ready on this PC</dt><dd>{hub ? `v${hub.version ?? '?'}` : 'nothing staged'}</dd>
          </dl>
          <p className="sub">
            {!online ? 'Connect Piblo to see if it needs an update.' : deviceInfo?.update_available ? 'Update available. Press Install update.' : 'Piblo is up to date.'}
          </p>
          {health?.fw_latest?.version && <span className="meta">Latest known: {health.fw_latest.version}</span>}
          {deviceInfo?.last_report?.stage && (
            <span className="meta">
              Last update: {deviceInfo.last_report.stage}
              {deviceInfo.last_report.error ? ` (${deviceInfo.last_report.error})` : ''}
            </span>
          )}
          <div className="row wrap">
            <button className="btn" onClick={check} disabled={checking}><Icon name="refresh" size={16} /> Check for updates</button>
            <button className="btn primary" onClick={install} disabled={!online || !deviceInfo?.update_available}>Install update</button>
          </div>
        </section>
      </div>

      <TodayPromises />

      <HomeReminders />

      <section className="card stack" aria-labelledby="beam-h">
        <h2 id="beam-h">Send to Piblo</h2>
        <p className="sub">Send a note or a link. It appears the next time Piblo checks in.</p>
        <div className="row">
          <input
            className="input"
            placeholder="Type a note or paste a link"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void send()}
            maxLength={2000}
            aria-label="Note to send"
          />
          <button className="btn primary" onClick={send} disabled={!note.trim()}><Icon name="send" size={16} /> Send</button>
        </div>
        <div className="list">
          {(beam.data?.messages ?? []).map((m) => (
            <div className="list-item" key={m.id}>
              <span className="tag">{m.direction === 'to_pocket' ? 'to Piblo' : 'from Piblo'}</span>
              <span className="grow detail selectable">{m.text}</span>
              <span className="meta">{ago(Math.max(0, Date.now() / 1000 - m.created))}</span>
            </div>
          ))}
          {!beam.loading && !(beam.data?.messages ?? []).length && <p className="meta">Nothing sent yet.</p>}
        </div>
      </section>
    </div>
  );
}

/** Home reminders: the next 14 days of bin days and home dates, with add / edit / delete. */
interface ReminderRule {
  id: string;
  kind: string;
  title: string;
  next_due: string | null;
  shift: boolean;
}

interface UpcomingResponse {
  ok: boolean;
  by_date: Record<string, ReminderRule[]>;
  days: number;
}

function dayLabel(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const tomorrow = new Date(today);
  tomorrow.setDate(tomorrow.getDate() + 1);
  if (d.getTime() === today.getTime()) return 'Today';
  if (d.getTime() === tomorrow.getTime()) return 'Tomorrow';
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

export function HomeReminders() {
  const { toast } = useStore();
  const { core } = useStore();
  const act = useAct();
  const running = core?.phase === 'running';
  const upcoming = usePoll<UpcomingResponse>('/promises/upcoming?days=14', running ? 30000 : 0);
  const [text, setText] = useState('');
  const [adding, setAdding] = useState(false);

  const rows = Object.entries(upcoming.data?.by_date ?? {}).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));

  const add = async () => {
    if (!text.trim()) return;
    setAdding(true);
    const r = await act('POST', '/promises/rules', { text: text.trim(), confirmed: true }, 'Reminder added');
    setAdding(false);
    if (r.ok) {
      setText('');
      upcoming.reload();
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm('Remove this reminder?')) return;
    const r = await act('DELETE', `/promises/rules/${id}`, undefined, 'Removed');
    if (r.ok) upcoming.reload();
  };

  const toggleShift = async (id: string, shift: boolean) => {
    const r = await act('PATCH', `/promises/rules/${id}`, { shift: !shift }, undefined);
    if (!r.ok) toast(r.message, true);
    else upcoming.reload();
  };

  if (upcoming.loading) return <div className="card"><Spinner /></div>;
  if (upcoming.error) return <Empty title="Could not load reminders"><p>{upcoming.error}</p></Empty>;

  return (
    <section className="card stack" aria-labelledby="reminders-h">
      <div className="row page-head">
        <h2 id="reminders-h">Home reminders</h2>
      </div>
      <p className="sub">
        Bin days, home dates and anything else you set up by voice (“black bin every other Tuesday”) or here.
      </p>
      <div className="row wrap">
        <input
          className="input"
          placeholder="e.g. black bin every other Tuesday, next one 18 Nov"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void add()}
          maxLength={200}
          aria-label="New home reminder"
        />
        <button className="btn primary" onClick={add} disabled={adding || !text.trim()}>
          {adding ? <Spinner /> : (<><Icon name="check" size={16} /> Add</>)}
        </button>
      </div>
      {rows.length === 0 && (
        <Empty title="No home reminders yet">
          <p>Say “black bin every other Tuesday” to Piblo, or add one above.</p>
        </Empty>
      )}
      <div className="list">
        {rows.map(([iso, items]) => (
          <div key={iso} className="list-group">
            <div className="row"><span className="meta">{dayLabel(iso)}</span></div>
            {items.map((item) => (
              <div key={item.id} className="list-item">
                <div className="grow">
                  <div className="row">
                    <span className="tag">{item.kind === 'bin' ? 'bin' : 'home'}</span>
                    {item.shift && <span className="tag meta">shifts after bank holidays</span>}
                    <span className="meta selectable" title={item.title}>{item.title}</span>
                  </div>
                  {item.next_due && <div className="detail meta">Next: {dayLabel(item.next_due)}</div>}
                </div>
                <div className="row wrap" style={{ gap: 'var(--s-2)' }}>
                  <Switch
                    checked={item.shift}
                    onChange={(v: boolean) => void toggleShift(item.id, v)}
                    label="Move collection after bank holidays"
                  />
                  <button className="btn sm danger" onClick={() => void remove(item.id)}>
                    <Icon name="bin" size={14} /> Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
