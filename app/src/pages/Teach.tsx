import { useState } from 'react';
import { PageHead } from '../components/ui';
import { useAct, usePoll } from '../lib/store';

type Session = { session_id: string; state: string; created_at: number; transcript: string; recording: string };
type Draft = { draft_id: string; skill_md: string; provider: string };

export function Teach() {
  const sessions = usePoll<{ sessions: Session[] }>('/teach', 5000);
  const skills = usePoll<{ skills: { slug: string; path: string }[] }>('/skills', 5000);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const act = useAct();
  const makeDraft = async (session_id: string) => {
    setBusy(true); setDraft(null);
    const result = await act<{ draft: Draft }>('POST', '/skills/draft/from-teach', { session_id, route: 'local' });
    if (result.ok) setDraft(result.data.draft);
    setBusy(false);
  };
  const approve = async () => {
    if (!draft) return;
    setBusy(true);
    const result = await act('POST', `/skills/draft/${encodeURIComponent(draft.draft_id)}/approve`, {}, 'Workflow saved');
    if (result.ok) { setDraft(null); skills.reload(); }
    setBusy(false);
  };
  return <div className="stack">
    <PageHead title="Show me once" sub="Turn a recorded PC demonstration into a reviewed workflow." />
    <section className="card stack">
      <h2>Record on Piblo</h2>
      <p>Open Capture → Teach Demo on your Piblo. Select a screen, explain the steps as you work, then stop recording. The recording is saved on this PC.</p>
      <p className="sub">The draft uses your configured local model when available. Review it against the recording: it cannot see the captured screen and may miss or misunderstand steps.</p>
      {busy && <p role="status">Working ? a local model may take up to two minutes.</p>}
      {sessions.error && <p role="alert">{sessions.error}</p>}
      {(sessions.data?.sessions ?? []).filter(s => s.state === 'ready' || s.state === 'ready_partial').map(s =>
        <div className="list-item" key={s.session_id}>
          <div className="grow"><strong>{new Date(s.created_at * 1000).toLocaleString()}</strong><div className="detail">{s.transcript || 'No narration detected; review the recording before saving.'}</div><div className="detail selectable">Recording: {s.recording}</div></div>
          <button className="btn" disabled={busy} onClick={() => void makeDraft(s.session_id)}>Create draft</button>
        </div>)}
      {!sessions.loading && !sessions.data?.sessions.some(s => s.state === 'ready' || s.state === 'ready_partial') && <p>No finished recordings yet.</p>}
    </section>
    {draft && <section className="card stack" aria-labelledby="draft-title">
      <h2 id="draft-title">Review before saving</h2>
      <p>Check every step and safety boundary against your recording. Saving does not run the workflow.</p>
      {draft.provider === 'deterministic-fallback' && <p role="status">Local model unavailable: this draft uses the narration or named marks directly.</p>}
      <pre className="selectable" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{draft.skill_md}</pre>
      <div className="row"><button className="btn" disabled={busy} onClick={() => setDraft(null)}>Discard</button><button className="btn primary" disabled={busy} onClick={() => void approve()}>Save reviewed workflow</button></div>
    </section>}
    <section className="card stack"><h2>Saved workflows</h2><p className="sub">Saved as skill files on this PC. They do not run automatically.</p>
      {(skills.data?.skills ?? []).map(s => <div key={s.slug}><strong>{s.slug}</strong><div className="detail selectable">{s.path}\SKILL.md</div></div>)}
      {!skills.loading && !skills.data?.skills.length && <p>No saved workflows yet.</p>}
      {skills.error && <p role="alert">{skills.error}</p>}
    </section>
  </div>;
}
