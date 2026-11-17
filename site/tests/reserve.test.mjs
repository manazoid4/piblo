import assert from 'node:assert/strict';
import test from 'node:test';

process.env.STRIPE_SECRET_KEY = 'sk_test_x';
process.env.STRIPE_PRICE_ID = 'price_test_123';
const { default: handler } = await import('../api/reserve.js');

const fakeRes = () => ({
  statusCode: 200, headers: {},
  setHeader(k, v) { this.headers[k.toLowerCase()] = v; },
  end() { return this; },
});
const stripe = (status, json) => {
  const calls = [];
  const impl = async (url, init) => { calls.push({ url, init }); return { ok: status < 300, status, json: async () => json }; };
  return { impl, calls };
};

test('GET creates a Checkout session and redirects to Stripe', async () => {
  const { impl, calls } = stripe(200, { url: 'https://checkout.stripe.com/c/pay/cs_live_abc' });
  const res = fakeRes();
  await handler({ method: 'GET' }, res, impl);
  assert.equal(res.statusCode, 303);
  assert.equal(res.headers.location, 'https://checkout.stripe.com/c/pay/cs_live_abc');
  assert.equal(calls.length, 1);
  const sent = new URLSearchParams(calls[0].init.body);
  assert.equal(sent.get('mode'), 'payment');
  assert.equal(sent.get('line_items[0][price]'), 'price_test_123');
  assert.equal(sent.get('line_items[0][quantity]'), '1');
  assert.equal(sent.get('success_url'), 'https://piblo.co.uk/reserved/?session_id={CHECKOUT_SESSION_ID}');
  assert.match(calls[0].init.headers.authorization, /^Bearer sk_test_x$/);
});

test('HEAD and other methods never create a session', async () => {
  const { impl, calls } = stripe(200, { url: 'https://checkout.stripe.com/x' });
  const head = fakeRes();
  await handler({ method: 'HEAD' }, head, impl);
  const post = fakeRes();
  await handler({ method: 'POST' }, post, impl);
  assert.equal(head.statusCode, 204);
  assert.equal(post.statusCode, 405);
  assert.equal(calls.length, 0);
});

test('Stripe errors and non-Stripe URLs fall back to the page, never a raw error', async () => {
  for (const [status, json] of [[500, {}], [200, { url: 'https://evil.example/pay' }], [200, {}]]) {
    const res = fakeRes();
    await handler({ method: 'GET' }, res, stripe(status, json).impl);
    assert.equal(res.statusCode, 303);
    assert.equal(res.headers.location, '/?reserve=failed#choose');
  }
});

test('missing configuration falls back without calling Stripe', async () => {
  const saved = process.env.STRIPE_PRICE_ID;
  process.env.STRIPE_PRICE_ID = '';
  const { impl, calls } = stripe(200, { url: 'https://checkout.stripe.com/x' });
  const res = fakeRes();
  await handler({ method: 'GET' }, res, impl);
  process.env.STRIPE_PRICE_ID = saved;
  assert.equal(res.headers.location, '/?reserve=failed#choose');
  assert.equal(calls.length, 0);
});
