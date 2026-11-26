import { useEffect, useRef, useState } from 'react';
import { CraftTask } from './CraftTask';
import { PageHead } from '../components/ui';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

export function Craft() {
  const api = window.piblo.craftTerminal!;
  const host = useRef<HTMLDivElement>(null);
  const [opened, setOpened] = useState(false);
  const [id, setId] = useState<number>();
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!opened || !host.current) return;
    const terminal = new Terminal({ scrollback: 2000, cursorBlink: true, fontSize: 14, theme: { background: '#1d1b19' } });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(host.current);
    fit.fit();
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let sessionId: number | undefined;
    const report = (error: unknown) => {
      if (active) setStatus(error instanceof Error ? error.message : 'Terminal unavailable.');
    };
    let pendingInput = '';
    function sendInput(data: string) {
      if (sessionId === undefined) {
        pendingInput = (pendingInput + data).slice(0, 65536);
      } else {
        for (let start = 0; start < data.length; start += 65536) {
          void api.write(sessionId, data.slice(start, start + 65536)).catch(report);
        }
      }
    }
    const input = terminal.onData(sendInput);
    const observer = new ResizeObserver(() => {
      fit.fit();
      if (sessionId !== undefined) void api.resize(sessionId, terminal.cols, terminal.rows).catch(report);
    });
    observer.observe(host.current);
    void (async () => {
      const session = await api.open(terminal.cols, terminal.rows);
      if (!active) return;
      sessionId = session.id;
      setId(session.id);
      if (pendingInput) sendInput(pendingInput);
      terminal.focus();
      let offset = session.end;
      async function read() {
        if (!active) return;
        try {
          const next = await api.read(session.id, offset);
          if (!active) return;
          offset = next.end;
          if (next.truncated) terminal.reset();
          const resume = () => {
            if (!active) return;
            if (next.exited) setStatus('PowerShell exited.');
            else timer = setTimeout(read, 35);
          };
          if (next.data) terminal.write(next.data, resume);
          else resume();
        } catch (error) { report(error); }
      }
      if (session.data) terminal.write(session.data, () => void read());
      else void read();
    })().catch(error => {
      report(error);
      if (active) setOpened(false);
    });
    return () => {
      active = false;
      clearTimeout(timer);
      observer.disconnect();
      input.dispose();
      terminal.dispose();
    };
  }, [opened]);

  async function close() {
    if (id === undefined) return;
    try {
      await api.close(id);
      setId(undefined);
      setOpened(false);
      setStatus('');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Could not close terminal.');
    }
  }

  return <>
    <PageHead title="Craft" sub="Draft a task, dictate it from Piblo, and run it in a real terminal." />
    <div className="stack">
      <CraftTask />
      <section className="card stack" aria-labelledby="craft-terminal">
        <div className="row">
          <h2 id="craft-terminal" className="grow">Terminal</h2>
          {opened
            ? <button className="btn sm" disabled={id === undefined} onClick={() => void close()}>Close terminal</button>
            : <button className="btn sm primary" onClick={() => { setStatus(''); setOpened(true); }}>Open terminal</button>}
        </div>
        {!opened && <p className="sub">PowerShell on this PC. It resizes with the window.</p>}
        {status && <p role="status" className="meta">{status}</p>}
        {opened && <div ref={host} aria-label="PowerShell terminal" style={{ height: 'clamp(260px, 60vh, 720px)', background: '#1d1b19', padding: 8, borderRadius: 'var(--r-sm)' }} />}
      </section>
    </div>
  </>;
}
