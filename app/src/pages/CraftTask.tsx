import { useEffect, useRef, useState } from 'react';
import type { CraftDraft, CraftTaskRecord } from '../../shared/types';

const blank = { model: '', prompt: '' };
const message = (error: unknown) => error instanceof Error ? error.message : 'Local task unavailable.';

export function CraftTask() {
  const api = window.piblo.craftTask!;
  const voice = window.piblo.craftVoice;
  const [draft, setDraft] = useState<CraftDraft>(blank);
  const [last, setLast] = useState<CraftTaskRecord['last']>(null);
  const [resume, setResume] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [modelStatus, setModelStatus] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [capture, setCapture] = useState<'idle' | 'arming' | 'waiting'>('idle');
  const mounted = useRef(false);
  const saving = useRef(0);
  const currentDraft = useRef(draft);
  const captureRevision = useRef(0);
  useEffect(() => {
    mounted.current = true;
    let active = true;
    void api.load().then(record => {
      if (!active) return;
      currentDraft.current = { model: record.model, prompt: record.prompt };
      setDraft(currentDraft.current);
      setLast(record.last);
      setResume(Boolean(record.prompt || record.last));
      setLoaded(true);
    }).catch(error => { if (active) setStatus(message(error)); });
    return () => {
      active = false;
      mounted.current = false;
      captureRevision.current++;
      void voice?.cancel().catch(() => {});
      void api.cancel().catch(() => {});
    };
  }, []);
  useEffect(() => {
    let active = true;
    setModelStatus('Looking for installed models…');
    void api.models().then(names => {
      if (!active) return;
      setModels(names);
      setModelStatus(names.length ? '' : 'No local models installed.');
    }).catch(() => {
      if (active) { setModels([]); setModelStatus('Ollama is unavailable on this PC.'); }
    });
    return () => { active = false; };
  }, [refresh]);

  function edit(next: CraftDraft) {
    currentDraft.current = next;
    setDraft(next);
    const revision = ++saving.current;
    void api.save(next).then(() => {
      if (mounted.current && revision === saving.current) setStatus('');
    }).catch(error => {
      if (mounted.current && revision === saving.current) setStatus(message(error));
    });
  }
  async function record() {
    if (!voice) return;
    const revision = ++captureRevision.current;
    setCapture('arming');
    try {
      await voice.arm();
      if (revision !== captureRevision.current) return;
      setCapture('waiting');
      setStatus('On Piblo, open Type on PC, choose Craft draft with C, then record.');
      while (revision === captureRevision.current) {
        await new Promise(resolve => setTimeout(resolve, 650));
        if (revision !== captureRevision.current) return;
        const result = await voice.read();
        if (revision !== captureRevision.current) return;
        if (result.status === 'waiting') continue;
        if (result.status === 'ready' && result.text.trim()) {
          const prior = currentDraft.current;
          const prompt = prior.prompt ? prior.prompt + '\n' + result.text.trim() : result.text.trim();
          if (prompt.length > 12000) throw new Error('The transcript does not fit in the current draft.');
          edit({ ...prior, prompt });
        } else {
          setStatus(result.status === 'expired' ? 'Capture expired. Try again.' : 'No speech was transcribed.');
        }
        break;
      }
    } catch (error) {
      if (revision === captureRevision.current) {
        void voice.cancel().catch(() => {});
        setStatus(message(error));
      }
    } finally {
      if (revision === captureRevision.current) setCapture('idle');
    }
  }
  function cancelCapture() {
    captureRevision.current++;
    setCapture('idle');
    setStatus('Capture cancelled.');
    void voice?.cancel().catch(() => {});
  }
  async function run() {
    setBusy(true);
    setStatus('Running locally…');
    try {
      const record = await api.run(draft);
      if (mounted.current) { setLast(record.last); setStatus('Result ready to review.'); }
    } catch (error) {
      if (mounted.current) setStatus(message(error));
    } finally { if (mounted.current) setBusy(false); }
  }
  return <section aria-label="Local task">
    <h2>Local task</h2>
    {resume ? <button className="btn" onClick={() => setResume(false)}>Continue last task</button> :
      <fieldset disabled={!loaded || busy} style={{ display: 'grid', gap: 8, border: 0, padding: 0 }}>
        <label htmlFor="craft-prompt">Task draft</label>
        <textarea id="craft-prompt" rows={3} maxLength={12000} value={draft.prompt} onChange={e => edit({ ...draft, prompt: e.target.value })} />
        <label htmlFor="craft-model">Local model</label>
        <select id="craft-model" value={models.includes(draft.model) ? draft.model : ''} onChange={e => edit({ ...draft, model: e.target.value })}>
          <option value="">Select an installed model</option>
          {models.map(name => <option key={name}>{name}</option>)}
        </select>
        <div><button className="btn" disabled={capture !== 'idle' || !draft.prompt.trim() || !models.includes(draft.model)} onClick={() => void run()}>Run locally</button>{' '}
          {voice && (capture === 'idle'
            ? <button className="btn" onClick={() => void record()}>Dictate with Piblo</button>
            : <button className="btn" onClick={cancelCapture}>Cancel capture</button>)}{' '}
          <button className="btn" onClick={() => setRefresh(n => n + 1)}>Refresh models</button></div>
      </fieldset>}
    {busy && <button className="btn" onClick={() => void api.cancel().catch(error => setStatus(message(error)))}>Cancel task</button>}
    {modelStatus && <p role="status">{modelStatus}</p>}
    {status && <p role="status">{status}</p>}
    {!resume && last && <section><h3>Last result</h3><p>{last.model} · {last.prompt}</p><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{last.result}</pre></section>}
  </section>;
}
