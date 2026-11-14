import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Icon, PageHead } from '../components/ui';
import { keyAction, SCREEN_H, SCREEN_W } from '../lib/logic';
import { createScreenPoller, DEVICE_BUTTONS, frameMessage, MAX_TYPE_CHARS, typePayload, type Frame } from '../lib/liveview';
import { useStore } from '../lib/store';

/** Shows Piblo's screen at 2 fps and sends keys, on-screen buttons, typed text and screenshots to it. */
export function LiveView() {
  const { core, pill, toast } = useStore();
  const running = core?.phase === 'running';
  const [frame, setFrame] = useState<Frame | null>(null);
  const [shot, setShot] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [windowVisible, setWindowVisible] = useState(true);
  const lastUrl = useRef<string | null>(null);
  const lastPng = useRef<Uint8Array | null>(null);

  const read = useCallback(async (): Promise<Frame> => {
    const r = await window.piblo.screenPng();
    if (!r.ok) return { ok: false, status: r.status, message: r.message };
    const bytes = new Uint8Array(r.data);
    lastPng.current = bytes;
    const url = URL.createObjectURL(new Blob([bytes], { type: 'image/png' }));
    if (lastUrl.current) URL.revokeObjectURL(lastUrl.current);
    lastUrl.current = url;
    setShot(url);
    return { ok: true, status: r.status };
  }, []);

  // The main process reports the real window state: document.hidden stays false when Electron
  // hides the window to the tray, so both signals are needed.
  useEffect(() => window.piblo.onWindowVisible(setWindowVisible), []);

  useEffect(() => {
    if (!running || !windowVisible) {
      if (!running) setFrame(null);
      return;
    }
    const poller = createScreenPoller({ read, isHidden: () => document.hidden, onFrame: setFrame });
    const onVisibility = () => (document.hidden ? poller.stop() : poller.start());
    poller.start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      poller.stop();
    };
  }, [running, read, windowVisible]);

  useEffect(
    () => () => {
      if (lastUrl.current) URL.revokeObjectURL(lastUrl.current);
    },
    [],
  );

  const send = useCallback(
    async (action: string) => {
      const r = await window.piblo.api('POST', '/core/cardputer/action', { action });
      if (!r.ok) toast(r.message, true);
      return r.ok;
    },
    [toast],
  );

  const typeText = async () => {
    const keys = typePayload(text);
    if (!keys) return;
    setSending(true);
    const r = await window.piblo.api('POST', '/core/cardputer/key', { keys });
    setSending(false);
    if (r.ok) {
      setText('');
      toast('Sent to Piblo');
    } else toast(r.message, true);
  };

  const save = async () => {
    if (!lastPng.current) return;
    const r = await window.piblo.saveScreenshot(lastPng.current);
    toast(r.message ?? (r.ok ? `Saved to ${r.path}` : 'Could not save the screenshot.'), !r.ok);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return; // leave app shortcuts alone
    const action = keyAction(e);
    if (!action) return;
    e.preventDefault();
    e.stopPropagation();
    void send(action);
  };

  const message = useMemo(() => (running ? frameMessage(frame, shot !== null) : 'Piblo on this PC is not running.'), [running, frame, shot]);
  const online = pill.state === 'connected';

  return (
    <div className="page">
      <PageHead
        title="Live View"
        sub={online ? 'Your Piblo is on the same Wi-Fi. Click the screen, then use your keyboard.' : 'Piblo is not answering yet. Turn it on and check it is on the same Wi-Fi as this PC.'}
        right={
          <button className="btn" onClick={save} disabled={!shot}>
            <Icon name="down" size={16} /> Save screenshot
          </button>
        }
      />

      <div className="screen-wrap">
        {shot ? (
          <img
            className="screen"
            src={shot}
            width={SCREEN_W}
            height={SCREEN_H}
            alt="Live view of your Piblo screen. Click it, then type or use the arrow keys."
            tabIndex={0}
            onKeyDown={onKey}
            draggable={false}
          />
        ) : (
          <div className="screen screen-blank" aria-hidden="true" />
        )}
        {message && (
          <p className="screen-msg" role="status">
            {message}
          </p>
        )}
      </div>

      <section className="card stack" aria-labelledby="keys-h">
        <h2 id="keys-h">Buttons</h2>
        <div className="keys">
          {DEVICE_BUTTONS.map((b) => (
            <button key={b.label} className="btn sm" disabled={!running} onClick={() => void send(b.action)}>
              {b.label}
            </button>
          ))}
        </div>
      </section>

      <section className="card stack" aria-labelledby="type-h">
        <h2 id="type-h">Type on Piblo</h2>
        <p className="sub">Sends the text to Piblo as if you typed it on its keyboard. Up to {MAX_TYPE_CHARS} characters.</p>
        <div className="row">
          <input
            className="input"
            value={text}
            maxLength={MAX_TYPE_CHARS}
            placeholder="Type here, then send"
            aria-label="Text to type on Piblo"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && typePayload(text)) void typeText();
            }}
          />
          <button className="btn primary" disabled={!running || sending || !typePayload(text)} onClick={typeText}>
            <Icon name="send" size={16} /> Send
          </button>
        </div>
      </section>
    </div>
  );
}
