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
interface SavedReference {
  id: string;
  kind: string;
  url?: string;
  title?: string;
  author?: string;
  tags?: string[];
  github_candidates?: string[];
  when?: string;
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

/** Share a Reel from Instagram to Piblo. We only claim link/caption capture, never that its video was watched. */
function FeedVault() {
  const act = useAct();
  const saved = usePoll<{ items: SavedReference[] }>('/library/recent?kind=instagram&limit=20', 8000);
  const saveRepo = async (url: string, fromUrl: string) => {
    const result = await act('POST', '/share', {
      url, text: `Found in a Reel shared to Piblo: ${fromUrl}`,
    }, 'Saved GitHub reference to Piblo on this PC');
    if (result.ok) saved.reload();
  };
  const rows = saved.data?.items ?? [];
  return (
    <section className="card stack" aria-labelledby="feed-h">
      <div className="row">
        <h2 id="feed-h" className="grow">Feed Vault</h2>
        <span className="tag">{rows.length} recent</span>
      </div>
      <p className="sub">Share an Instagram post or Reel to Piblo from your phone. It saves the link and any text you share. Video understanding is not active.</p>
      {saved.error && <p role="alert">{saved.error}</p>}
      <div className="list">
        {rows.map((item) => (
          <div className="list-item" key={item.id}>
            <div className="grow">
              <div className="title selectable">{item.title || item.url || 'Saved Instagram link'}</div>
              <div className="detail">
                {item.tags?.includes('captions_provided') ? 'Captions supplied' : item.tags?.includes('transcript_provided') ? 'Transcript supplied' : 'Link or shared text only — not watched'}
              </div>
            </div>
            <div className="row wrap">
              {(item.github_candidates ?? []).map((repo) => (
                <button className="btn sm" key={repo} onClick={() => void saveRepo(repo, item.url || '')}>Save GitHub repo</button>
              ))}
              {item.url && <button className="btn sm ghost" onClick={() => void window.piblo.openExternal(item.url!)}>Open source</button>}
            </div>
          </div>
        ))}
        {!saved.loading && !rows.length && <Empty title="Nothing shared yet"><p>In Instagram, choose Share, then Piblo. You can also save a Reel link below.</p></Empty>}
      </div>
    </section>
  );
}

function Library() {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [url, setUrl] = useState('');
  const [reason, setReason] = useState('');
  const [receipt, setReceipt] = useState('');
  const act = useAct();

  const search = async () => {
    if (!q.trim()) return;
    const r = await act<{ results: Hit[] }>('GET', `/library/search?q=${encodeURIComponent(q.trim())}&limit=20`);
    if (r.ok) setHits(r.data.results);
  };
  const ingest = async () => {
    if (!/^https?:\/\//i.test(url.trim())) return;
    const r = await act<{ kind: string; unified_memory?: { state?: string } }>(
      'POST', '/share', { url: url.trim(), text: reason.trim() || url.trim(), title: '' },
    );
    if (r.ok) {
      setUrl('');
      setReason('');
      const state = r.data.unified_memory?.state;
      setReceipt(state === 'local_checkout'
        ? 'Saved in Piblo and local Unified Memory. GitHub sync is still pending.'
        : r.data.kind === 'github'
          ? 'Saved on this PC. Configure Unified Memory to sync GitHub references.'
          : 'Saved in Piblo on this PC.');
    }
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
        <input className="input" placeholder="Paste a GitHub repo or Instagram Reel URL (https://...)" value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void ingest()} aria-label="Link to save" />
        <button className="btn" onClick={ingest} disabled={!/^https?:\/\//i.test(url.trim())}>Save link</button>
      </div>
      <input className="input" placeholder="Why save this? (optional)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} aria-label="What could Piblo use this for?" />
      {receipt && <p role="status" className="meta">{receipt}</p>}
    </section>
  );
}

export function Memory() {
  return (
    <div className="page">
      <PageHead title="Memory" sub="Everything your Piblo heard and everything you saved, in one place." />
      <Inbox />
      <FeedVault />
      <Library />
      <FileVault />
    </div>
  );
}
