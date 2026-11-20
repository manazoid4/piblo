import assert from 'node:assert/strict';
import test from 'node:test';
import handler from '../api/reserve.js';

const fakeRes = () => ({
  statusCode: 200, headers: {},
  setHeader(k, v) { this.headers[k.toLowerCase()] = v; },
  end() { return this; },
});

test('GET and HEAD redirect to the Stripe Payment Link', () => {
  for (const method of ['GET', 'HEAD']) {
    const res = fakeRes();
    handler({ method }, res);
    assert.equal(res.statusCode, 303);
    assert.match(res.headers.location, /^https:\/\/buy\.stripe\.com\//);
  }
});

test('other methods are refused', () => {
  const res = fakeRes();
  handler({ method: 'POST' }, res);
  assert.equal(res.statusCode, 405);
  assert.equal(res.headers.location, undefined);
});
