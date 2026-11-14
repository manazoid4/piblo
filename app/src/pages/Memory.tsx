import { useState } from 'react';
import { Empty, Icon, PageHead } from '../components/ui';
import { useAct, usePoll } from '../lib/store';
import { FileVault } from '../components/FileVault';

interface Dump {
  id: string;
  summary?: unknown;
  status?: string;
  project?: string;
  created?: string;
  source?: string;
  claimed_by?: string;
}
interface Hit {
  id: string;
  kind: string;
  url?: string;
  author?: string;
  project?: string;
  snippet?: string;
  saved?: string;
}

const text = (v: unknown): string => (v == null ? '' : typeof v === 'string' ? v : typeof v === 'object' ? String((v as Record<string, unknown>).text ?? (v as Record<string, unknown>).title ?? (v as Record<string, unknown>).summary ?? '') : String(v));

function ReadThought({ id, summary }: { id: string; summary: string }) {
  const [open, setOpen] = useState(false);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const act = useAct();
  const read = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (transcript !== null) return;
    setLoading(true);
    setError('');
    const r = await act<{ transcript: string }>('GET', `/dumps/${encodeURIComponent(id)}`);
    if (r.ok) setTranscript(r.data.transcript);
    else setError(r.message);
    setLoading(false);
  };
  return <div>
    {!open && <div className="title selectable">{summary}</div>}
    <button className="btn sm ghost" aria-expanded={open} aria-controls={`thought-${id}`} onClick={() => void read()} disabled={loading}>{open ? 'Close' : 'Read'}</button>
    {open && <div id={`thought-${id}`} className="selectable" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
      <strong>Full thought</strong>
      {loading ? <p role="status">Loading thought...</p> : error ? <p role="alert">{error} Close and choose Read to try again.</p> : <p>{transcript || 'No transcript saved.'}</p>}
    </div>}
  </div>;
}

function Inbox() {
  const [status, setStatus] = useState('');
  const dumps = usePoll<{ dumps: Dump[] }>(`/dumps${status ? `?status=${status}` : ''}`, 8000);
  const [draft, setDraft] = useState('');
  const act = useAct();

  const save = async () => {
    if (!draft.trim()) return;
    const r = await act('POST', '/dumps', { text: draft.trim(), source: 'web' }, 'Saved to your inbox');
    if (r.ok) {
      setDraft('');
      dumps.reload();
    }
  };
  const done = async (id: string) => {
    const r = await act('POST', `/dumps/${encodeURIComponent(id)}/done`, { agent: 'desktop', note: '' }, 'Marked done');
    if (r.ok) dumps.reload();
  };

  const list = dumps.data?.dumps ?? [];
  return (
    <section className="card stack" aria-labelledby="inbox-h">
      <div className="row">
        <h2 id="inbox-h" className="grow">Saved thoughts</h2>
        <div className="seg" role="group" aria-label="Filter">
          {[['', 'All'], ['new', 'New'], ['claimed', 'Claimed'], ['done', 'Done']].map(([v, l]) => (
            <button key={v} aria-pressed={status === v} onClick={() => setStatus(v)}>{l}</button>
          ))}
        </div>
      </div>
      <div className="row">
        <textarea className="input" rows={2} placeholder="Save a thought, idea or reminder" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void save(); }} aria-label="New thought" />
        <button className="btn primary" onClick={save} disabled={!draft.trim()}>Save</button>
      </div>
      {dumps.error && <p className="meta">{dumps.error}</p>}
      <div className="list">
        {list.map((d) => (
          <div className="list-item" key={d.id}>
            <div className="grow">
              <ReadThought id={d.id} summary={text(d.summary) || '(no summary)'} />
              <div className="detail">{[d.project || 'no project', d.source, d.claimed_by && `claimed by ${d.claimed_by}`].filter(Boolean).join(' / ')}</div>
            </div>
            <span className="tag">{d.status ?? 'new'}</span>
            {d.status !== 'done' && <button className="btn sm" onClick={() => void done(d.id)}>Done</button>}
          </div>
        ))}
        {!dumps.loading && !list.length && (
          <Empty title="Nothing here yet">
            <p>Hold the talk key on your Piblo, or save a thought above.</p>
          </Empty>
        )}
      </div>
    </section>
  );
}

function Library() {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [url, setUrl] = useState('');
  const act = useAct();

  const search = async () => {
    if (!q.trim()) return;
    const r = await act<{ results: Hit[] }>('GET', `/library/search?q=${encodeURIComponent(q.trim())}&limit=20`);
    if (r.ok) setHits(r.data.results);
  };
  const ingest = async () => {
    if (!/^https?:\/\//i.test(url.trim())) return;
    const r = await act('POST', '/ingest', { kind: 'url', url: url.trim() }, 'Saved to your library');
    if (r.ok) setUrl('');
  };

  return (
    <section className="card stack" aria-labelledby="lib-h">
      <h2 id="lib-h">Library</h2>
      <p className="sub">Search everything you have saved: links, posts and notes.</p>
      <div className="row">
        <input className="input" placeholder="Search your library" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void search()} aria-label="Search library" />
        <button className="btn" onClick={search} disabled={!q.trim()}><Icon name="search" size={16} /> Search</button>
      </div>
      {hits && (
        <div className="list">
          {hits.map((h) => (
            <div className="list-item" key={h.id}>
              <div className="grow">
                <div className="title selectable">{h.snippet || h.url || h.id}</div>
                <div className="detail">{[h.kind, h.author, h.project].filter(Boolean).join(' / ')}</div>
              </div>
              {h.url && <button className="btn sm ghost" onClick={() => void window.piblo.openExternal(h.url!)}>Open</button>}
            </div>
          ))}
          {!hits.length && <Empty title="No matches"><p>Try fewer or different words.</p></Empty>}
        </div>
      )}
      <div className="row">
        <input className="input" placeholder="Save a link to your library (https://...)" value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void ingest()} aria-label="Link to save" />
        <button className="btn" onClick={ingest} disabled={!/^https?:\/\//i.test(url.trim())}>Save link</button>
      </div>
    </section>
  );
}

export function Memory() {
  return (
    <div className="page">
      <PageHead title="Memory" sub="Everything your Piblo heard and everything you saved, in one place." />
      <Inbox />
      <Library />
      <FileVault />
    </div>
  );
}
