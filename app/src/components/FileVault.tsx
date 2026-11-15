import { useEffect, useState } from 'react';
import { Empty } from './ui';
import { useAct, usePoll, useStore } from '../lib/store';

interface VaultFile { id: string; name: string; path: string; mime: string; size: number }
interface VaultList { enabled: boolean; files: VaultFile[]; truncated: boolean }
interface VaultContent { name: string; mime: string; content_base64: string }
interface Preview { name: string; url?: string; text?: string }

export function FileVault() {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [fetching, setFetching] = useState<string | null>(null);
  const vault = usePoll<VaultList>(`/vault?q=${encodeURIComponent(search)}`, 0);
  const act = useAct();
  const { toast } = useStore();

  useEffect(() => () => { if (preview?.url) URL.revokeObjectURL(preview.url); }, [preview]);

  const fetchFile = async (file: VaultFile) => {
    setFetching(file.id);
    try {
      const result = await act<VaultContent>('GET', `/vault/${encodeURIComponent(file.id)}`);
      if (!result.ok) return;
      const { name, mime, content_base64 } = result.data;
      const binary = atob(content_base64);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      const blob = new Blob([bytes], { type: mime });
      const downloadUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = name;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
      if (['image/jpeg', 'image/png', 'image/webp'].includes(mime)) {
        setPreview({ name, url: URL.createObjectURL(blob) });
      } else if (mime === 'text/plain') {
        setPreview({ name, text: new TextDecoder('utf-8', { fatal: true }).decode(bytes) });
      } else setPreview(null);
    } catch {
      toast('Could not decode this file.', true);
    } finally {
      setFetching(null);
    }
  };

  return (
    <section className="card stack" aria-labelledby="vault-h">
      <h2 id="vault-h">Fetch from Home</h2>
      <p className="sub">Find files by name in your selected folder.</p>
      {vault.error && <p className="meta">{vault.error}</p>}
      {vault.data && !vault.data.enabled ? <p className="meta">No folder configured for files.</p> : vault.data?.enabled && <>
        <form className="row" onSubmit={(event) => { event.preventDefault(); setSearch(query.trim()); }}>
          <input className="input" aria-label="Search file names" placeholder="Search file names" value={query} onChange={(event) => setQuery(event.target.value)} />
          <button className="btn" type="submit">Search</button>
        </form>
        <div className="list">
          {vault.data.files.map((file) => <div className="list-item" key={file.id}>
            <div className="grow"><div className="title selectable">{file.name}</div><div className="detail selectable">{file.path}</div></div>
            <button className="btn sm" onClick={() => void fetchFile(file)} disabled={fetching !== null}>{fetching === file.id ? 'Fetching…' : 'Fetch'}</button>
          </div>)}
          {!vault.data.files.length && <Empty title="No files found"><p>Try a different file name.</p></Empty>}
        </div>
        {vault.data.truncated && <p className="meta">More files match. Narrow your search.</p>}
        {preview && <div className="stack"><h3>{preview.name}</h3>
          {preview.url && <img src={preview.url} alt={preview.name} style={{ maxWidth: '100%', maxHeight: 400, objectFit: 'contain' }} />}
          {preview.text !== undefined && <pre className="selectable" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 400, overflow: 'auto' }}>{preview.text}</pre>}
        </div>}
      </>}
    </section>
  );
}
