import { useState } from 'react';
import { Setting, Switch } from './ui';
import { useAct, usePoll } from '../lib/store';

type FlowSettings = {
  input_mode: 'paste' | 'type';
  press_enter: boolean;
  cleanup: 'auto' | 'rules' | 'groq';
  stt: 'auto' | 'parakeet' | 'whisper';
  effective_cleanup: string;
  has_groq_key: boolean;
};
type FlowStatus = { count: number; avg_total_ms: number; p95_total_ms: number; recent: { chars: number }[]; errors: string[] };

/** One plain line summarising dictation history, or why it is unavailable. */
export function flowStatusLine(status: FlowStatus | null, error: string | null): string {
  if (error) return `Could not read Flow history: ${error}`;
  if (!status) return 'No dictations yet.';
  const n = status.count;
  if (!n) return 'No dictations yet.';
  const last = status.errors[status.errors.length - 1];
  return `${n} dictation${n === 1 ? '' : 's'} · average ${status.avg_total_ms} ms${last ? ` · last problem: ${last}` : ''}`;
}

const CLEANUP_SUB: Record<string, string> = {
  rules: 'Tidied on this PC. Nothing leaves it.',
  groq: 'Sent to Groq to add punctuation. Needs a Groq key.',
  local: 'Tidied on this PC. Nothing leaves it.',
};

/** Dictation controls: how Flow inserts text and cleans it up. */
export function FlowSection() {
  const act = useAct();
  const settings = usePoll<FlowSettings>('/flow/settings', 0);
  const status = usePoll<FlowStatus>('/flow/status', 15000);
  const dictionary = usePoll<{ words: string[]; entries?: { term: string; aliases: string[] }[] }>('/flow/dictionary', 0);
  const [word, setWord] = useState('');

  const s = settings.data;
  const entries = dictionary.data?.entries ?? (dictionary.data?.words ?? []).map((w) => ({ term: w, aliases: [] }));
  const save = async (p: Partial<FlowSettings>) => {
    const r = await act<void>('PATCH', '/flow/settings', p, 'Flow updated');
    if (r.ok) settings.reload();
  };
  const add = async () => {
    const r = await act<{ words: string[] }>('POST', '/flow/dictionary', { word: word.trim() }, 'Word added');
    if (r.ok) {
      setWord('');
      dictionary.reload();
    }
  };
  const remove = async (w: string) => {
    const r = await act<{ words: string[] }>('DELETE', `/flow/dictionary/${encodeURIComponent(w)}`, undefined, 'Word removed');
    if (r.ok) dictionary.reload();
  };

  return (
    <section className="card" aria-labelledby="flow-h">
      <h2 id="flow-h">Dictation</h2>
      <Setting
        title="How spoken text arrives"
        sub={s?.input_mode === 'type' ? 'Typed into the window that has focus, character by character.' : 'Pasted into the window that has focus. Works in most apps.'}
      >
        <div className="seg" role="group" aria-label="Insert mode">
          {([['paste', 'Paste'], ['type', 'Type']] as const).map(([v, l]) => (
            <button key={v} aria-pressed={(s?.input_mode ?? 'paste') === v} disabled={!s} onClick={() => void save({ input_mode: v })}>{l}</button>
          ))}
        </div>
      </Setting>
      <Setting title="Press Enter after pasting" sub={s?.input_mode === 'type' ? 'Only applies when pasting.' : 'Sends Enter once the text is in, so it submits or sends straight away.'}>
        <Switch label="Press Enter after pasting" checked={s?.press_enter ?? false} disabled={!s || s.input_mode === 'type'} onChange={(v) => void save({ press_enter: v })} />
      </Setting>
      <Setting title="Clean up the words" sub={s ? (s.cleanup === 'auto' ? `Auto: currently ${s.effective_cleanup === 'groq' ? 'using the cloud' : 'on this PC'}.` : CLEANUP_SUB[s.effective_cleanup] ?? '') : 'Loading...'}>
        <div className="seg" role="group" aria-label="Cleanup">
          {([['auto', 'Auto'], ['rules', 'On this PC'], ['groq', 'Cloud']] as const).map(([v, l]) => (
            <button key={v} aria-pressed={(s?.cleanup ?? 'auto') === v} disabled={!s || (v === 'groq' && !s.has_groq_key)} onClick={() => void save({ cleanup: v })}>{l}</button>
          ))}
        </div>
      </Setting>
      <Setting title="Speech recognition" sub="Auto picks whichever model Piblo Core has ready.">
        <div className="seg" role="group" aria-label="Speech">
          {([['auto', 'Auto'], ['parakeet', 'Parakeet'], ['whisper', 'Whisper']] as const).map(([v, l]) => (
            <button key={v} aria-pressed={(s?.stt ?? 'auto') === v} disabled={!s} onClick={() => void save({ stt: v })}>{l}</button>
          ))}
        </div>
      </Setting>
      <Setting title="Words Piblo should get right" sub="Names and jargon Flow keeps exactly as written. Add “Rampage: ram page” to also catch a mishearing.">
        <div className="row" style={{ flex: '0 1 250px', minWidth: 0 }}>
          <input className="input" aria-label="New word" placeholder="Add a word, or Term: misheard" value={word} maxLength={120} onChange={(e) => setWord(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && word.trim()) void add(); }} />
          <button className="btn" disabled={!word.trim()} onClick={add}>Add</button>
        </div>
      </Setting>
      {!!entries.length && (
        <div className="row wrap" style={{ paddingTop: 'var(--s-2)' }}>
          {entries.slice(0, 40).map((e) => (
            <button
              key={e.term}
              className="tag"
              title={e.aliases.length ? `${e.term} — also heard as ${e.aliases.join(', ')}. Remove.` : `Remove ${e.term}`}
              onClick={() => void remove(e.term)}
            >
              {e.term}
              {!!e.aliases.length && <span className="meta"> · also “{e.aliases[0]}”{e.aliases.length > 1 ? ` +${e.aliases.length - 1}` : ''}</span>} ×
            </button>
          ))}
        </div>
      )}
      <p className="meta" style={{ paddingTop: 'var(--s-2)' }}>{flowStatusLine(status.data, status.error)}</p>
    </section>
  );
}