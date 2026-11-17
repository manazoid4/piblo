import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

process.env.RESEND_API_KEY = 're_test_key';
process.env.RESEND_SEGMENT_ID = 'seg_piblo';
process.env.SIGNUP_NOTIFY_EMAIL = 'owner@example.com';
const { default: handler } = await import('../api/waitlist.js');

function fakeReq(body, { json = true, method = 'POST' } = {}) {
  const req = new EventEmitter();
  req.method = method;
  req.headers = json
    ? { 'content-type': 'application/json', accept: 'application/json' }
    : { 'content-type': 'application/x-www-form-urlencoded', accept: 'text/html' };
  req.destroy = () => {};
  setImmediate(() => {
    if (body !== undefined) req.emit('data', Buffer.from(json ? JSON.stringify(body) : new URLSearchParams(body).toString()));
    req.emit('end');
  });
  return req;
}

function fakeRes() {
  return {
    statusCode: 200, headers: {}, body: '',
    setHeader(k, v) { this.headers[k.toLowerCase()] = v; },
    end(chunk) { if (chunk) this.body += chunk; return this; },
  };
}

// Writes go in `calls`; the duplicate lookup (GET) goes in `lookups` and, by default,
// answers "not in any segment" so every signup looks new.
function recordingFetch(status = 200, text = '', segments = []) {
  const calls = [];
  const lookups = [];
  const impl = async (url, init) => {
    if (init.method === 'GET') {
      lookups.push(url);
      return { ok: true, status: 200, text: async () => JSON.stringify({ data: segments }) };
    }
    calls.push({ url, headers: init.headers, body: JSON.parse(init.body) });
    return { ok: status < 300, status, text: async () => text };
  };
  return { calls, lookups, impl };
}

test('a repeat signup already in the Piblo segment is saved but sends no second alert', async () => {
  const f = recordingFetch(200, '', [{ id: 'seg_other' }, { id: 'seg_piblo' }]);
  const res = fakeRes();
  await handler(fakeReq({ email: 'Ali@example.co.uk', consent: true }), res, f.impl);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(f.lookups, ['https://api.resend.com/contacts/ali%40example.co.uk/segments?limit=100']);
  assert.equal(f.calls.length, 1, 'contact saved (re-subscribes), no owner alert');
  assert.equal(f.calls[0].url, 'https://api.resend.com/contacts');
});

test('a contact known only from another list still alerts as a new Piblo signup', async () => {
  const f = recordingFetch(200, '', [{ id: 'seg_other' }]);
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: true }), fakeRes(), f.impl);
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[1].url, 'https://api.resend.com/emails');
});

test('a brand-new contact (lookup 404) is saved and alerts', async () => {
  const calls = [];
  const impl = async (url, init) => {
    if (init.method === 'GET') return { ok: false, status: 404, text: async () => '{"name":"not_found"}' };
    calls.push(url);
    return { ok: true, status: 200, text: async () => '' };
  };
  await handler(fakeReq({ email: 'new@example.co.uk', consent: true }), fakeRes(), impl);
  assert.deepEqual(calls, ['https://api.resend.com/contacts', 'https://api.resend.com/emails']);
});

test('a failed duplicate lookup still alerts (never silently drops a real signup)', async () => {
  const calls = [];
  const impl = async (url, init) => {
    if (init.method === 'GET') throw new Error('network');
    calls.push(url);
    return { ok: true, status: 200, text: async () => '' };
  };
  const res = fakeRes();
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: true }), res, impl);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(calls, ['https://api.resend.com/contacts', 'https://api.resend.com/emails']);
});

test('a consenting valid email goes into the Piblo segment only', async () => {
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: ' Ali@Example.co.uk ', first_name: ' Ali ', consent: true }), res, f.impl);
  assert.equal(res.statusCode, 200);
  assert.equal(f.calls.length, 2, 'contact, then the owner alert');
  assert.equal(f.calls[0].url, 'https://api.resend.com/contacts');
  assert.deepEqual(f.calls[0].body, { email: 'ali@example.co.uk', first_name: 'Ali', unsubscribed: false, segments: [{ id: 'seg_piblo' }], properties: { referral_code: f.calls[0].body.properties.referral_code } });
});

test('owner alert goes from the Piblo sender to the configured inbox', async () => {
  const f = recordingFetch();
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: true }), fakeRes(), f.impl);
  const mail = f.calls[1];
  assert.equal(mail.url, 'https://api.resend.com/emails');
  assert.equal(mail.body.from, 'Piblo <signups@mazworks.uk>');
  assert.deepEqual(mail.body.to, ['owner@example.com']);
});

test('a failed alert never breaks the signup', async () => {
  const impl = async (url) => (url.endsWith('/emails') ? { ok: false, status: 403, text: async () => '' } : { ok: true, status: 200, text: async () => '' });
  const res = fakeRes();
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: true }), res, impl);
  assert.equal(res.statusCode, 200);
});

test('no alert address configured means no alert, signup still works', async () => {
  const saved = process.env.SIGNUP_NOTIFY_EMAIL;
  delete process.env.SIGNUP_NOTIFY_EMAIL;
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: true }), res, f.impl);
  process.env.SIGNUP_NOTIFY_EMAIL = saved;
  assert.equal(res.statusCode, 200);
  assert.equal(f.calls.length, 1);
});

test('no consent, no contact', async () => {
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: 'ali@example.co.uk' }), res, f.impl);
  assert.equal(res.statusCode, 400);
  assert.equal(JSON.parse(res.body).reason, 'no-consent');
  assert.equal(f.calls.length, 0);
});

test('a bad email is refused before anything reaches Resend', async () => {
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: 'not-an-email', consent: true }), res, f.impl);
  assert.equal(res.statusCode, 400);
  assert.equal(f.calls.length, 0);
});

test('old honeypot name (cached pages) is still treated as a bot', async () => {
  const f = recordingFetch(); const res = fakeRes();
  await handler(fakeReq({ email: 'bot@example.com', consent: true, nod_hp_field: 'spam' }), res, f.impl);
  assert.equal(f.calls.length, 0);
  assert.equal(res.statusCode, 200);
});

test('honeypot looks successful but sends nothing', async () => {
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: 'bot@example.com', consent: true, piblo_hp_field: 'spam' }), res, f.impl);
  assert.equal(res.statusCode, 200);
  assert.equal(f.calls.length, 0);
});

test('duplicate signup counts as success', async () => {
  const f = recordingFetch(409, 'Contact already exists');
  const res = fakeRes();
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: true }), res, f.impl);
  assert.equal(res.statusCode, 200);
});

test('Resend failure is reported, not hidden', async () => {
  const f = recordingFetch(500, 'boom');
  const res = fakeRes();
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: true }), res, f.impl);
  assert.equal(res.statusCode, 502);
});

test('missing segment refuses rather than dumping into the shared list', async () => {
  const saved = process.env.RESEND_SEGMENT_ID;
  delete process.env.RESEND_SEGMENT_ID;
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: true }), res, f.impl);
  process.env.RESEND_SEGMENT_ID = saved;
  assert.equal(res.statusCode, 502);
  assert.equal(f.calls.length, 0);
});

test('no-JS form post redirects back to the form', async () => {
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: 'yes' }, { json: false }), res, f.impl);
  assert.equal(res.statusCode, 303);
  assert.match(res.headers.location, /^\/\?subscribed=1&code=[a-z0-9]{8}#waitlist$/);
});

test('oversized body is rejected', async () => {
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: 'ali@example.co.uk', consent: true, first_name: 'x'.repeat(5000) }), res, f.impl);
  assert.equal(res.statusCode, 413);
  assert.equal(f.calls.length, 0);
});

test('GET is not allowed', async () => {
  const res = fakeRes();
  await handler(fakeReq(undefined, { method: 'GET' }), res, recordingFetch().impl);
  assert.equal(res.statusCode, 405);
});

test('signup returns a stable referral code and stores it, plus a valid referred_by', async () => {
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: 'new@example.com', consent: true, ref: ' ABCD1234 ' }), res, f.impl);
  const out = JSON.parse(res.body);
  assert.match(out.ref, /^[a-z0-9]{8}$/);
  assert.deepEqual(f.calls[0].body.properties, { referral_code: out.ref, referred_by: 'abcd1234' });
  const again = fakeRes();
  await handler(fakeReq({ email: 'new@example.com', consent: true }), again, recordingFetch().impl);
  assert.equal(JSON.parse(again.body).ref, out.ref, 'repeat signup keeps the same link');
});

test('junk or self-referrals are dropped and never reach Resend', async () => {
  for (const ref of ['x', '../etc', '<script>', 'a'.repeat(40), 42]) {
    const f = recordingFetch();
    await handler(fakeReq({ email: 'junk@example.com', consent: true, ref }), fakeRes(), f.impl);
    assert.deepEqual(Object.keys(f.calls[0].body.properties), ['referral_code'], String(ref));
  }
  const f = recordingFetch();
  const res = fakeRes();
  await handler(fakeReq({ email: 'me@example.com', consent: true }), res, f.impl);
  const mine = JSON.parse(res.body).ref;
  const g = recordingFetch();
  await handler(fakeReq({ email: 'me@example.com', consent: true, ref: mine }), fakeRes(), g.impl);
  assert.equal(g.calls[0].body.properties.referred_by, undefined);
});

test('if Resend rejects the referral properties the signup is still saved without them', async () => {
  const calls = [];
  const impl = async (url, init) => {
    if (init.method === 'GET') return { ok: true, status: 200, text: async () => '{"data":[]}' };
    const body = JSON.parse(init.body);
    calls.push(body);
    const bad = body.properties;
    return { ok: !bad, status: bad ? 422 : 200, text: async () => '' };
  };
  const res = fakeRes();
  await handler(fakeReq({ email: 'p@example.com', consent: true, ref: 'abcd1234' }), res, impl);
  assert.equal(res.statusCode, 200);
  assert.equal(calls.length >= 2 && calls[1].properties, undefined);
});

test('honeypot and consent still block before any referral work', async () => {
  const f = recordingFetch();
  await handler(fakeReq({ email: 'b@example.com', consent: true, piblo_hp_field: 'x', ref: 'abcd1234' }), fakeRes(), f.impl);
  const r = fakeRes();
  await handler(fakeReq({ email: 'b@example.com', ref: 'abcd1234' }), r, f.impl);
  assert.equal(r.statusCode, 400);
  assert.equal(f.calls.length, 0);
});


test('only recognised campaign sources appear in owner alerts', async () => {
  for (const [source, expected] of [['reddit', 'Source: reddit'], ['discord', 'Source: discord'], ['x', 'Source: x'], ['reddit\nBCC: attacker', '']]) {
    const f = recordingFetch();
    await handler(fakeReq({ email: 'visitor@example.com', consent: true, source }), fakeRes(), f.impl);
    const body = f.calls[1].body.text;
    if (expected) assert.ok(body.includes(expected));
    else assert.ok(!body.includes('Source:') && !body.includes('attacker'));
    assert.equal(Object.hasOwn(f.calls[0].body, 'source'), false);
    assert.equal(Object.hasOwn(f.calls[0].body.properties, 'source'), false);
  }
});
