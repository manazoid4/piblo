import { describe, expect, it, vi } from 'vitest';
import { createApiClient } from '../electron/api-client';
import { CODES_PRIVACY_LINE, describeImport, looksLikeOtpLink, normalizeKeyCode, parseMatchWords, accountName } from '../shared/auth';

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const mk = (f: unknown) => createApiClient({ baseUrl: () => 'http://127.0.0.1:8787', token: () => 'tok', fetch: f as typeof fetch });
const call = (f: ReturnType<typeof vi.fn>, i = 0) => {
  const [url, init] = f.mock.calls[i] as unknown as [string, RequestInit];
  return { url, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined };
};

describe('auth api client', () => {
  it('lists codes and keys with GET and the bearer token', async () => {
    const f = vi.fn(async () => json(200, { ok: true, accounts: [], locked: false }));
    const r = await mk(f).auth.codes();
    expect(r.ok && r.data.accounts).toEqual([]);
    expect(call(f)).toMatchObject({ url: 'http://127.0.0.1:8787/auth/codes', method: 'GET' });
    const init = (f.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok');
  });

  it('imports a trimmed link as {uri}', async () => {
    const f = vi.fn(async () => json(200, { ok: true, added: [], skipped: [] }));
    await mk(f).auth.importCode('  otpauth://totp/GitHub:me?secret=ABC&issuer=GitHub \n');
    expect(call(f)).toMatchObject({ url: 'http://127.0.0.1:8787/auth/codes/import', method: 'POST', body: { uri: 'otpauth://totp/GitHub:me?secret=ABC&issuer=GitHub' } });
  });

  it('renames with PATCH and encodes the id in the path', async () => {
    const f = vi.fn(async () => json(200, { ok: true }));
    await mk(f).auth.updateCode('a/b c', { issuer: 'GitHub', match: ['github'] });
    expect(call(f)).toMatchObject({ url: 'http://127.0.0.1:8787/auth/codes/a%2Fb%20c', method: 'PATCH', body: { issuer: 'GitHub', match: ['github'] } });
  });

  it('always sends confirm:true when deleting a code or removing a key', async () => {
    const f = vi.fn(async () => json(200, { ok: true }));
    const c = mk(f);
    await c.auth.deleteCode('c1');
    await c.auth.removeKey('k1');
    expect(call(f, 0)).toMatchObject({ url: 'http://127.0.0.1:8787/auth/codes/c1', method: 'DELETE', body: { confirm: true } });
    expect(call(f, 1)).toMatchObject({ url: 'http://127.0.0.1:8787/auth/key/k1', method: 'DELETE', body: { confirm: true } });
  });

  it('reads and writes prefs', async () => {
    const f = vi.fn(async () => json(200, { ok: true, autosuggest: true, require_key_for_show: false }));
    await mk(f).auth.prefs();
    await mk(f).auth.setPrefs({ autosuggest: false, require_key_for_show: true });
    expect(call(f, 0)).toMatchObject({ url: 'http://127.0.0.1:8787/auth/prefs', method: 'GET' });
    expect(call(f, 1)).toMatchObject({ method: 'PUT', body: { autosuggest: false, require_key_for_show: true } });
  });

  it('enrols a key with {code}', async () => {
    const f = vi.fn(async () => json(200, { ok: true, key: { id: 'k1', uid_tail: 'A1B2', name: '', enrolled_at: 1 } }));
    const r = await mk(f).auth.enrollKey(' PK1-04A1-AA-BB ');
    expect(r.ok).toBe(true);
    expect(call(f)).toMatchObject({ url: 'http://127.0.0.1:8787/auth/key/enroll', method: 'POST', body: { code: 'PK1-04A1-AA-BB' } });
  });

  it('turns Core 422 {error, message} bodies into a readable message', async () => {
    const f = vi.fn(async () => json(422, { error: 'invalid_uri', message: 'Not an otpauth link.' }));
    const r = await mk(f).auth.importCode('otpauth://nope');
    expect(r).toMatchObject({ ok: false, status: 422, code: 'bad-request', message: 'Not an otpauth link.' });
    const g = vi.fn(async () => json(422, { error: 'unsupported_type' }));
    const r2 = await mk(g).auth.importCode('otpauth://hotp/x?secret=A');
    expect(!r2.ok && r2.message).toMatch(/time-based/);
  });
});

describe('codes and keys helpers', () => {
  it('accepts only otpauth links', () => {
    expect(looksLikeOtpLink(' otpauth://totp/x?secret=A ')).toBe(true);
    expect(looksLikeOtpLink('otpauth-migration://offline?data=abc')).toBe(true);
    expect(looksLikeOtpLink('https://example.com')).toBe(false);
    expect(looksLikeOtpLink('JBSWY3DPEHPK3PXP')).toBe(false);
  });

  it('parses match words: trimmed, lower-case, unique, capped', () => {
    expect(parseMatchWords(' GitHub, sign in,github\n, ')).toEqual(['github', 'sign in']);
    expect(parseMatchWords('')).toEqual([]);
    expect(parseMatchWords(Array.from({ length: 15 }, (_, i) => `w${i}`).join(','))).toHaveLength(10);
  });

  it('normalises the PK1 enrolment string and rejects anything else', () => {
    expect(normalizeKeyCode(' pk1-04a1b2c3d4e5f6-00112233445566778899aabbccddeeff-ffeeddccbbaa99887766554433221100 ')).toBe(
      'PK1-04A1B2C3D4E5F6-00112233445566778899AABBCCDDEEFF-FFEEDDCCBBAA99887766554433221100',
    );
    expect(normalizeKeyCode('PK1-JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP')).toBe('PK1-JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP');
    expect(normalizeKeyCode('hello')).toBeNull();
    expect(normalizeKeyCode('PK1-')).toBeNull();
  });

  it('describes an import in one sentence', () => {
    const acc = { id: 'a', issuer: 'X', label: 'y', digits: 6, period: 30, algorithm: 'SHA1' as const, match: [], added_at: 0 };
    expect(describeImport({ added: [acc, acc], skipped: [] })).toBe('Added 2 accounts.');
    expect(describeImport({ added: [acc], skipped: [{ label: 'a', reason: 'duplicate' }, { label: 'b', reason: 'invalid_secret' }] })).toBe('Added 1 account. 1 already here, 1 skipped.');
    expect(describeImport({ added: [], skipped: [{ label: 'a', reason: 'duplicate' }] })).toBe('Nothing new was added. 1 already here.');
  });

  it('names accounts and keeps the honest privacy line free of hype and internal names', () => {
    expect(accountName({ issuer: 'GitHub', label: 'me' })).toBe('GitHub (me)');
    expect(CODES_PRIVACY_LINE).toBe('Codes stay encrypted on this PC. Piblo only types one after you press YES.');
    expect(CODES_PRIVACY_LINE).not.toMatch(/unhackable|Nod|MAZ|Cardputer|Core/i);
  });
});
