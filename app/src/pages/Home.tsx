import { useState } from 'react';
import { Empty, Icon, PageHead } from '../components/ui';
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
  const device = usePoll<{ device_fw_version?: string | null; staged_fw_version?: string | null }>('/core/cardputer/status', 15000);
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
          <p className="sub">
            {hub ? `Version ${hub.version ?? '?'} is ready on this PC.` : 'No update ready on this PC.'}{' '}
            {online && hub ? (deviceInfo?.update_available ? 'It is newer than what Piblo runs.' : 'Piblo is up to date.') : ''}
          </p>
          {health?.fw_latest?.version && <span className="meta">Latest known: {health.fw_latest.version}</span>}
          {device.data?.device_fw_version && (
            <span className="meta">
              Piblo v{device.data.device_fw_version} · {device.data.staged_fw_version ? `update ${device.data.staged_fw_version} ready` : 'up to date'}
            </span>
          )}
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
