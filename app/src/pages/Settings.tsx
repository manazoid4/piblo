import { useEffect, useState } from 'react';
import { FlowSection } from '../components/FlowSection';
import type { AppSettings, CoreEnvView, EnvKey } from '../../shared/types';
import { PageHead, Setting, Switch } from '../components/ui';
import { useAct, usePoll, useStore } from '../lib/store';
import { useTheme } from '../App';

function KeyField({ label, envKey, isSet, onSaved }: { label: string; envKey: EnvKey; isSet: boolean; onSaved(msg: string): void }) {
  const [v, setV] = useState('');
  const save = async () => {
    const r = await window.piblo.env.set(envKey, v.trim());
    onSaved(r.message);
    if (r.ok) setV('');
  };
  return (
    <Setting title={label} sub={isSet ? 'A key is saved. Paste a new one to replace it.' : 'Not set'}>
      <input className="input" style={{ width: 220 }} type="password" autoComplete="off" placeholder={isSet ? 'Saved' : 'Paste key'} value={v} onChange={(e) => setV(e.target.value)} aria-label={`${label} key`} />
      <button className="btn" disabled={!v.trim()} onClick={save}>Save</button>
    </Setting>
  );
}

export function Settings() {
  const { core, health, toast, setPairing, pairing, refreshDevice } = useStore();
  const act = useAct();
  const [app, setApp] = useState<AppSettings | null>(null);
  const [env, setEnv] = useState<CoreEnvView | null>(null);
  const [code, setCode] = useState<{ code: string; ttl: number } | null>(null);
  const [diag, setDiag] = useState('');
  const memorySettings = usePoll<{ memory_repo_path: string }>('/dev/memory-settings', 0);
  const [memoryPath, setMemoryPath] = useState<string | null>(null);

  const loadEnv = () => void window.piblo.env.get().then(setEnv);
  useEffect(() => {
    void window.piblo.settings.get().then(setApp);
    loadEnv();
  }, []);

  const { theme, setTheme } = useTheme();
  const patch = async (p: Partial<AppSettings>) => setApp(await window.piblo.settings.set(p));
  const saved = (msg: string) => {
    toast(msg);
    loadEnv();
  };

  const pair = async () => {
    setPairing(true);
    const r = await window.piblo.device.pair();
    setPairing(false);
    toast(r.message, !r.ok);
    refreshDevice();
  };
  const phone = async () => {
    const r = await act<{ code: string; expires_in_seconds: number }>('POST', '/pair/start');
    if (r.ok) setCode({ code: r.data.code, ttl: r.data.expires_in_seconds });
  };
  const diagnostics = async () => {
    const r = await act<unknown>('GET', '/diagnostics');
    if (r.ok) setDiag(JSON.stringify(r.data, null, 2));
  };
  const setRoute = async (route: string) => {
    const r = await window.piblo.env.set('MAZ_DEFAULT_ROUTE', route);
    saved(r.message);
  };
  const restart = async () => {
    const s = await window.piblo.core.restart();
    toast(s.phase === 'running' ? 'Core restarted' : s.message, s.phase !== 'running');
  };

  const running = core?.phase === 'running';
  return (
    <div className="page">
      <PageHead title="Settings" sub="Pairing, brains, privacy and the app itself." />

      <section className="card" aria-labelledby="pair-h">
        <h2 id="pair-h">Pairing</h2>
        <Setting
          title="Connect your Piblo"
          sub="On Piblo, open More, then Connect to computer. It shows a 6-digit code and this window shows a card to Allow or deny. Nothing connects until you press Allow."
        >
          <span className="tag">Waiting for a request</span>
        </Setting>
        <Setting title="Connect over USB instead" sub="If Piblo cannot use Wi-Fi, plug it into this PC with a USB cable. This sends it this PC's address and a private token. No Wi-Fi details are touched.">
          <button className="btn" onClick={pair} disabled={pairing || !running}>{pairing ? 'Connecting...' : 'Connect over USB'}</button>
        </Setting>
        <Setting title="Pair a phone" sub={code ? `Open this PC's Core address with /pair on your phone and enter ${code.code}. It works for ${Math.round(code.ttl / 60)} minutes.` : 'Get a one-time code for the phone approval page.'}>
          {code ? <span className="tag selectable" style={{ fontSize: 'var(--text-lg)', letterSpacing: '0.1em' }}>{code.code}</span> : <button className="btn" onClick={phone} disabled={!running}>Get a code</button>}
        </Setting>
      </section>

      <section className="card" aria-labelledby="brain-h">
        <h2 id="brain-h">Brain and privacy</h2>
        <Setting title="Where answers come from" sub={env?.route === 'local' || !env?.route ? 'Private: questions are answered on this PC and never leave it.' : env.route === 'cloud' ? 'Cloud: questions are sent to online models.' : 'Auto: tries this PC first, then the cloud.'}>
          <div className="seg" role="group" aria-label="Route">
            {([['local', 'Private'], ['auto', 'Auto'], ['cloud', 'Cloud']] as const).map(([v, l]) => (
              <button key={v} aria-pressed={(env?.route || 'local') === v} disabled={!env?.envFile} onClick={() => void setRoute(v)}>{l}</button>
            ))}
          </div>
        </Setting>
        <KeyField label="Groq" envKey="MAZ_GROQ_API_KEY" isSet={!!env?.hasGroqKey} onSaved={saved} />
        <KeyField label="OpenRouter" envKey="MAZ_OPENROUTER_API_KEY" isSet={!!env?.hasOpenRouterKey} onSaved={saved} />
        <KeyField label="Fish Audio (voice)" envKey="MAZ_FISH_API_KEY" isSet={!!env?.hasFishKey} onSaved={saved} />
        <p className="meta" style={{ paddingTop: 'var(--s-2)' }}>Keys are stored in Core's own settings file on this PC and are never shown again. Changes apply after Core restarts.</p>
      </section>

      <FlowSection />

      <section className="card" aria-labelledby="app-h">
              <h2 id="app-h">This app</h2>
              <Setting title="Theme" sub="Light, dark, or follow your system setting.">
                <div className="seg" role="group" aria-label="Theme">
                  {(['light', 'dark', 'system'] as const).map((v) => (
                    <button
                      key={v}
                      aria-pressed={theme === v}
                      onClick={() => void setTheme(v as 'light' | 'dark' | 'system')}
                    >
                      {v === 'light' ? 'Light' : v === 'dark' ? 'Dark' : 'System'}
                    </button>
                  ))}
                </div>
              </Setting>
              <Setting title="Keep running in the tray" sub="Closing the window hides it. Quit from the tray icon.">
          <Switch label="Keep running in the tray" checked={app?.closeToTray ?? true} onChange={(v) => void patch({ closeToTray: v })} />
        </Setting>
        <Setting title="Start with Windows" sub="Open quietly in the tray when you sign in.">
          <Switch label="Start with Windows" checked={app?.startWithWindows ?? false} onChange={(v) => void patch({ startWithWindows: v })} />
        </Setting>
        <Setting title="Welcome tour" sub="Walk through connecting your Piblo again.">
          <button className="btn" onClick={() => { void patch({ onboardingDone: false }); location.hash = '#/welcome'; }}>Show again</button>
        </Setting>
        <Setting title="Keyboard" sub="Ctrl+1 to Ctrl+6 jump between sections. Tab moves through everything.">
          <span className="tag">Ctrl+1..6</span>
        </Setting>
      </section>

      <section className="card" aria-labelledby="adv-h">
        <h2 id="adv-h">Advanced</h2>
        <Setting title="Read-only memory folder" sub="Optional. Leave empty to hide the Piblo Dev memory view.">
          <input className="input" aria-label="Memory folder" value={memoryPath ?? memorySettings.data?.memory_repo_path ?? ''} onChange={(event) => setMemoryPath(event.target.value)} />
          <button className="btn" disabled={!running || memoryPath === null} onClick={() => void act('PUT', '/dev/memory-settings', { memory_repo_path: memoryPath }).then((r) => { if (r.ok) { setMemoryPath(null); memorySettings.reload(); } })}>Save</button>
        </Setting>
        <Setting title="Piblo on this PC" sub={running ? `Running${health?.version ? ` v${health.version}` : ''} on port ${core?.port}${core?.managed ? ' (started by this app)' : ' (already running)'}` : core?.message ?? ''}>
          <button className="btn" onClick={restart}>Restart</button>
          <button className="btn" onClick={() => void window.piblo.core.openLogs()}>Open log</button>
        </Setting>
        <Setting title="Core folder" sub={core?.installDir}>
          <button className="btn" onClick={diagnostics} disabled={!running}>Run diagnostics</button>
        </Setting>
        {diag && <pre className="selectable meta" style={{ margin: 0, maxHeight: 220, overflow: 'auto', fontFamily: 'var(--font-mono)' }}>{diag}</pre>}
        <Setting title="Phone control page" sub="The approval page you can open from your phone.">
          <button className="btn" disabled={!running} onClick={() => void window.piblo.openExternal(`http://127.0.0.1:${core?.port}/control/`)}>Open</button>
        </Setting>
        <p className="meta" style={{ paddingTop: 'var(--s-2)' }}>Piblo app {window.piblo.version}</p>
      </section>
    </div>
  );
}
