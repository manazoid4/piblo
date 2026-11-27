// Piblo Codes and Piblo Key: types and pure helpers shared by the main process, the page and tests.
// Contract: docs/protocol/AUTH.md. Core never returns a secret or a code from these routes.

export interface CodeAccount {
  id: string;
  issuer: string;
  label: string;
  digits: number;
  period: number;
  algorithm: 'SHA1' | 'SHA256' | 'SHA512';
  match: string[];
  added_at: number | string;
}

export interface CodesList {
  ok: boolean;
  accounts: CodeAccount[];
  locked?: boolean;
}

export interface ImportResult {
  ok: boolean;
  added: CodeAccount[];
  skipped: { label: string; reason: string }[];
}

export interface AuthPrefs {
  autosuggest: boolean;
  require_key_for_show: boolean;
}

export interface PibloKey {
  id: string;
  uid_tail: string;
  name: string;
  enrolled_at: number | string;
  last_tap_at?: number | string | null;
}

export const MAX_KEYS = 3;

export const AUTH_PATHS = {
  codes: '/auth/codes',
  importCode: '/auth/codes/import',
  code: (id: string) => `/auth/codes/${encodeURIComponent(id)}`,
  prefs: '/auth/prefs',
  keys: '/auth/key',
  enroll: '/auth/key/enroll',
  key: (id: string) => `/auth/key/${encodeURIComponent(id)}`,
} as const;

export const CODES_PRIVACY_LINE = 'Codes stay encrypted on this PC. Piblo only types one after you press YES.';

/** True for the two link kinds Core can import. Anything else is not sent. */
export function looksLikeOtpLink(text: string): boolean {
  return /^otpauth(-migration)?:\/\//i.test(text.trim());
}

/** Match words from a comma/newline list: trimmed, lower-cased, unique, at most 10. */
export function parseMatchWords(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[,\n]/)) {
    const w = part.trim().toLowerCase();
    if (w) seen.add(w.slice(0, 40));
  }
  return [...seen].slice(0, 10);
}

/** The enrolment string printed on the card insert: PK1-<UID>-<K_meta>-<K_file>, or the packed base32 form. */
export function normalizeKeyCode(text: string): string | null {
  const s = text.replace(/\s+/g, '');
  if (/^PK1-[0-9A-F]+(-[0-9A-F]+){2}$/i.test(s)) return s.toUpperCase();
  if (/^PK1-[A-Z2-7=]{20,}$/i.test(s)) return s.toUpperCase();
  return null;
}

/** One plain sentence for the result of an import. */
export function describeImport(r: Pick<ImportResult, 'added' | 'skipped'>): string {
  const n = r.added.length;
  const added = n === 0 ? 'Nothing new was added' : `Added ${n} account${n === 1 ? '' : 's'}`;
  const dup = r.skipped.filter((s) => s.reason === 'duplicate').length;
  const other = r.skipped.length - dup;
  const bits: string[] = [];
  if (dup) bits.push(`${dup} already here`);
  if (other) bits.push(`${other} skipped`);
  return bits.length ? `${added}. ${bits.join(', ')}.` : `${added}.`;
}

export function accountName(a: Pick<CodeAccount, 'issuer' | 'label'>): string {
  return a.issuer ? (a.label ? `${a.issuer} (${a.label})` : a.issuer) : a.label || 'Account';
}
