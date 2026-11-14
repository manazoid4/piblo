import { useState } from 'react';
import { useAct, usePoll, useStore } from '../lib/store';

interface Shortcut { phrase: string; actions: string[] }
interface Shortcuts { shortcuts: Shortcut[] }

const ACTIONS = [
  ['desktop', 'Show desktop'], ['play_pause', 'Play / pause'], ['mute', 'Toggle mute'],
  ['volume_down', 'Volume down'], ['volume_up', 'Volume up'],
  ['previous_track', 'Previous track'], ['next_track', 'Next track'],
] as const;

export function VoiceShortcuts() {
  const saved = usePoll<Shortcuts>('/voice-shortcuts', 0);
  const act = useAct();
  const { toast } = useStore();
  const [phrase, setPhrase] = useState('');
  const [actions, setActions] = useState<string[]>(['desktop']);
  const [editing, setEditing] = useState<Shortcut | null>(null);
  const [busy, setBusy] = useState(false);

  const reset = () => { setPhrase(''); setActions(['desktop']); setEditing(null); };
  const update = async (remove = false, selected: Shortcut | null = editing) => {
    setBusy(true);
    try {
      const latest = await act<Shortcuts>('GET', '/voice-shortcuts');
      if (!latest.ok) return;
      const list = latest.data.shortcuts;
      if (selected && JSON.stringify(list.find((s) => s.phrase === selected.phrase)) !== JSON.stringify(selected)) {
        toast('This shortcut changed elsewhere. Reload it before editing.', true);
        saved.reload();
        return;
      }
      const name = phrase.trim().toLowerCase();
      if (!remove && list.some((s) => s.phrase === name && s.phrase !== selected?.phrase)) {
        toast('Choose a different phrase; that one is already used.', true);
        return;
      }
      const shortcuts = list.filter((s) => s.phrase !== selected?.phrase);
      if (!remove) shortcuts.push({ phrase: name, actions });
      const result = await act<Shortcuts>('PUT', '/voice-shortcuts', { shortcuts }, remove ? 'Shortcut deleted.' : 'Shortcut saved.');
      if (result.ok) { reset(); saved.reload(); }
    } finally { setBusy(false); }
  };

  return <section className="card stack" aria-labelledby="voice-shortcuts-h">
    <h2 id="voice-shortcuts-h">Voice shortcuts</h2>
    <p className="sub">Say a phrase to run these PC actions in order. Toggle mute changes the current sound state.</p>
    {saved.error && <p role="alert">Could not load shortcuts: {saved.error}</p>}
    {saved.loading && <p role="status">Loading shortcuts...</p>}
    {saved.data && <>
      <div className="list">
        {saved.data.shortcuts.map((s) => <div className="list-item" key={s.phrase}>
          <div className="grow"><div className="title">{s.phrase}</div><div className="detail">{s.actions.map((a) => ACTIONS.find(([id]) => id === a)?.[1] ?? a).join(' → ')}</div></div>
          <button className="btn sm" type="button" disabled={busy} aria-label={`Edit ${s.phrase}`} onClick={() => { setEditing(s); setPhrase(s.phrase); setActions([...s.actions]); }}>Edit</button>
          <button className="btn sm" type="button" disabled={busy} aria-label={`Delete ${s.phrase}`} onClick={() => { if (window.confirm(`Delete shortcut “${s.phrase}”?`)) void update(true, s); }}>Delete</button>
        </div>)}
      </div>
      <form className="stack" onSubmit={(event) => { event.preventDefault(); void update(); }}>
        <label>Spoken phrase <input className="input" value={phrase} onChange={(event) => setPhrase(event.target.value)} maxLength={60} required /></label>
        {actions.map((action, index) => <div className="row wrap" key={index}>
          <label>Action {index + 1} <select className="input" value={action} onChange={(event) => setActions(actions.map((a, i) => i === index ? event.target.value : a))}>
            {ACTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select></label>
          {actions.length > 1 && <button className="btn sm" type="button" aria-label={`Remove action ${index + 1}`} onClick={() => setActions(actions.filter((_, i) => i !== index))}>Remove</button>}
        </div>)}
        <div className="row wrap">
          <button className="btn" type="button" disabled={busy || actions.length >= 10} onClick={() => setActions([...actions, 'desktop'])}>Add action</button>
          <button className="btn primary" type="submit" disabled={busy}>Save shortcut</button>
          {editing && <button className="btn" type="button" disabled={busy} onClick={reset}>Cancel edit</button>}
        </div>
      </form>
    </>}
  </section>;
}
