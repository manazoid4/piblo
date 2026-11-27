import { useState } from 'react';
import {
  AUTH_PATHS,
  CODES_PRIVACY_LINE,
  MAX_KEYS,
  accountName,
  describeImport,
  looksLikeOtpLink,
  normalizeKeyCode,
  parseMatchWords,
  type AuthPrefs,
  type CodeAccount,
  type CodesList,
  type ImportResult,
  type PibloKey,
} from '../../shared/auth';
import { Setting, Switch } from './ui';
import { useAct, usePoll } from '../lib/store';

function when(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === '') return 'never used';
  const d = new Date(typeof v === 'number' && v < 1e12 ? v * 1000 : v);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString();
}

function AccountRow({ a, onChanged }: { a: CodeAccount; onChanged(): void }) {
  const act = useAct();
  const [edit, setEdit] = useState(false);
  const [issuer, setIssuer] = useState(a.issuer);
  const [label, setLabel] = useState(a.label);
  const [match, setMatch] = useState(a.match.join(', '));
  const [confirm, setConfirm] = useState(false);

  const save = async () => {
    const r = await act('PATCH', AUTH_PATHS.code(a.id), { issuer: issuer.trim(), label: label.trim(), match: parseMatchWords(match) }, 'Saved');
    if (r.ok) {
      setEdit(false);
      onChanged();
    }
  };
  const remove = async () => {
    const r = await act('DELETE', AUTH_PATHS.code(a.id), { confirm: true }, 'Removed');
    if (r.ok) onChanged();
    setConfirm(false);
  };

  if (edit) {
    return (
      <div className="setting" style={{ flexWrap: 'wrap' }}>
        <input className="input" aria-label="Name" value={issuer} onChange={(e) => setIssuer(e.target.value)} placeholder="Name, e.g. GitHub" />
        <input className="input" aria-label="Account" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Account" />
        <input className="input" aria-label="Match words" style={{ minWidth: 220 }} value={match} onChange={(e) => setMatch(e.target.value)} placeholder="Window words, e.g. github, sign in" />
        <button className="btn" onClick={() => void save()}>Save</button>
        <button className="btn ghost" onClick={() => setEdit(false)}>Cancel</button>
      </div>
    );
  }
  return (
    <Setting title={a.issuer || a.label || 'Account'} sub={`${a.issuer ? a.label : ''}${a.match.length ? `${a.issuer && a.label ? ' · ' : ''}Looks for: ${a.match.join(', ')}` : ''}` || undefined}>
      {confirm ? (
        <>
          <button className="btn sm danger" onClick={() => void remove()} aria-label={`Confirm remove ${accountName(a)}`}>Remove</button>
          <button className="btn sm ghost" onClick={() => setConfirm(false)}>Keep</button>
        </>
      ) : (
        <>
          <button className="btn sm" onClick={() => setEdit(true)} aria-label={`Edit ${accountName(a)}`}>Edit</button>
          <button className="btn sm danger" onClick={() => setConfirm(true)} aria-label={`Remove ${accountName(a)}`}>Remove</button>
        </>
      )}
    </Setting>
  );
}

function KeyRow({ k, onChanged }: { k: PibloKey; onChanged(): void }) {
  const act = useAct();
  const [confirm, setConfirm] = useState(false);
  const remove = async () => {
    const r = await act('DELETE', AUTH_PATHS.key(k.id), { confirm: true }, 'Piblo Key removed');
    if (r.ok) onChanged();
    setConfirm(false);
  };
  return (
    <Setting title={k.name || `Piblo Key ...${k.uid_tail}`} sub={`Ends ${k.uid_tail}. Added ${when(k.enrolled_at)}. Last tap: ${when(k.last_tap_at)}.`}>
      {confirm ? (
        <>
          <button className="btn sm danger" onClick={() => void remove()}>Remove key</button>
          <button className="btn sm ghost" onClick={() => setConfirm(false)}>Keep</button>
        </>
      ) : (
        <button className="btn sm danger" onClick={() => setConfirm(true)} aria-label={`Remove Piblo Key ${k.uid_tail}`}>Remove</button>
      )}
    </Setting>
  );
}

/** Codes & Keys: your 2FA accounts and Piblo Key cards. The page never receives a secret or a code. */
export function CodesKeysSection() {
  const act = useAct();
  const codes = usePoll<CodesList>(AUTH_PATHS.codes, 0);
  const keys = usePoll<{ keys: PibloKey[] }>(AUTH_PATHS.keys, 0);
  const prefs = usePoll<AuthPrefs>(AUTH_PATHS.prefs, 0);
  const [link, setLink] = useState('');
  const [keyCode, setKeyCode] = useState('');
  const [note, setNote] = useState('');

  const accounts = codes.data?.accounts ?? [];
  const keyList = keys.data?.keys ?? [];
  const linkOk = looksLikeOtpLink(link);
  const keyNorm = normalizeKeyCode(keyCode);

  const doImport = async () => {
    const r = await act<ImportResult>('POST', AUTH_PATHS.importCode, { uri: link.trim() });
    if (r.ok) {
      setNote(describeImport(r.data));
      setLink('');
      codes.reload();
    }
  };
  const enroll = async () => {
    if (!keyNorm) return;
    const r = await act('POST', AUTH_PATHS.enroll, { code: keyNorm }, 'Piblo Key added');
    if (r.ok) {
      setKeyCode('');
      keys.reload();
    }
  };
  const setPref = async (p: Partial<AuthPrefs>) => {
    if (!prefs.data) return;
    const r = await act<AuthPrefs>('PUT', AUTH_PATHS.prefs, { autosuggest: prefs.data.autosuggest, require_key_for_show: prefs.data.require_key_for_show, ...p });
    if (r.ok) prefs.reload();
  };

  return (
    <section className="card" aria-labelledby="codes-h">
      <h2 id="codes-h">Codes &amp; Keys</h2>
      <p className="meta">{CODES_PRIVACY_LINE}</p>

      <Setting title="Add codes" sub="Paste an otpauth:// link, or an otpauth-migration:// link from your authenticator's export. Many accounts at once is fine.">
        <input className="input" style={{ width: 260 }} type="password" autoComplete="off" spellCheck={false} aria-label="Authenticator link" placeholder="otpauth://..." value={link} onChange={(e) => setLink(e.target.value)} />
        <button className="btn" disabled={!linkOk} onClick={() => void doImport()}>Add</button>
      </Setting>
      {link.trim() !== '' && !linkOk && <p className="meta">That does not look like an otpauth:// link.</p>}
      {note && <p className="meta" role="status">{note}</p>}

      {codes.loading ? (
        <p className="meta">Loading...</p>
      ) : codes.error ? (
        <p className="meta">Could not load codes. {codes.error}</p>
      ) : accounts.length === 0 ? (
        <p className="meta">No codes yet. Add one above and it shows up on Piblo under Codes.</p>
      ) : (
        accounts.map((a) => <AccountRow key={a.id} a={a} onChanged={codes.reload} />)
      )}

      <Setting title="Suggest a code on login pages" sub="When a window looks like a 2FA prompt for one of your accounts, Piblo asks if you want the code. Nothing is typed until you press YES.">
        <Switch label="Suggest codes" checked={prefs.data?.autosuggest ?? true} disabled={!prefs.data} onChange={(v) => void setPref({ autosuggest: v })} />
      </Setting>
      <Setting title="Require Piblo Key to show codes" sub="Showing a code on Piblo's screen also needs a tap of your Piblo Key on your phone.">
        <Switch label="Require Piblo Key to show codes" checked={prefs.data?.require_key_for_show ?? false} disabled={!prefs.data || keyList.length === 0} onChange={(v) => void setPref({ require_key_for_show: v })} />
      </Setting>

      <h3 style={{ paddingTop: 'var(--s-3)' }}>Piblo Key</h3>
      <p className="meta">Each tap is signed by the card and can only be used once. Tap it on your phone to confirm sensitive approvals.</p>
      {keyList.map((k) => <KeyRow key={k.id} k={k} onChanged={keys.reload} />)}
      {keyList.length < MAX_KEYS ? (
        <Setting title="Add a Piblo Key" sub="Type the code printed on the sealed insert that came with the card. It starts with PK1-.">
          <input className="input" style={{ width: 260 }} type="password" autoComplete="off" spellCheck={false} aria-label="Piblo Key code" placeholder="PK1-..." value={keyCode} onChange={(e) => setKeyCode(e.target.value)} />
          <button className="btn" disabled={!keyNorm} onClick={() => void enroll()}>Add key</button>
        </Setting>
      ) : (
        <p className="meta">You have {MAX_KEYS} keys, the most Piblo can hold. Remove one to add another.</p>
      )}
    </section>
  );
}
