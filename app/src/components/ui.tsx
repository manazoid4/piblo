import type { ReactNode } from 'react';

const paths: Record<string, string> = {
  device: 'M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm0 4h10M10 17h4',
  screen: 'M3 4h18v12H3zM8 20h8M12 16v4',
  down: 'M12 4v11m0 0l-4-4m4 4l4-4M4 20h16',
  ask: 'M4 5h16v11H9l-5 4V5z',
  memory: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 5v5l3 2',
  actions: 'M13 3L5 14h6l-1 7 8-11h-6l1-7z',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-2a7.5 7.5 0 0 0 0-2l2-1.5-2-3.5-2.3 1a7.6 7.6 0 0 0-1.7-1L15 3h-4l-.4 2.5a7.6 7.6 0 0 0-1.7 1l-2.3-1-2 3.5L6.6 11a7.5 7.5 0 0 0 0 2l-2 1.5 2 3.5 2.3-1c.5.4 1.1.7 1.7 1L11 21h4l.4-2.5c.6-.3 1.2-.6 1.7-1l2.3 1 2-3.5-2-1.5z',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  plug: 'M9 3v5m6-5v5M7 8h10v4a5 5 0 0 1-10 0V8zm5 9v4',
  warn: 'M12 4l9 16H3L12 4zm0 6v4m0 3v.01',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6',
  send: 'M4 12l16-8-6 16-3-7-7-1z',
  moon: 'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm9 16l-4-4',
  dev: 'M8 9l-4 3 4 3M16 9l4 3-4 3M13.5 6l-3 12',
};

export function Icon({ name, size = 18 }: { name: keyof typeof paths | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={paths[name] ?? ''} />
    </svg>
  );
}

export function Spinner() {
  return <span className="spinner" role="status" aria-label="Loading" />;
}

export function PageHead({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="row page-head">
      <div className="grow">
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {right}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function Banner({ kind = 'warn', title, children, action }: { kind?: 'warn' | 'bad' | 'ok'; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={`banner ${kind === 'warn' ? '' : kind}`} role={kind === 'bad' ? 'alert' : 'status'}>
      <Icon name={kind === 'ok' ? 'check' : 'warn'} />
      <div className="grow">
        <strong>{title}</strong>
        {children && <span>{children}</span>}
      </div>
      {action}
    </div>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange(v: boolean): void; label: string; disabled?: boolean }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className="switch" disabled={disabled} onClick={() => onChange(!checked)} />;
}

export function Setting({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <div className="setting">
      <div className="grow">
        <strong>{title}</strong>
        {sub && <span>{sub}</span>}
      </div>
      {children}
    </div>
  );
}
