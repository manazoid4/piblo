// £5 refundable reservation for Piblo Standard (batch one). Creates a Stripe Checkout
// session server-side and redirects to Stripe's hosted page, so card details never touch
// this site and the secret key never reaches a browser. GET on purpose: a plain link works
// without JavaScript, and the site's form-action CSP would block a form redirect to Stripe.
const SESSIONS_URL = 'https://api.stripe.com/v1/checkout/sessions';
const TIMEOUT_MS = 10000;
const ORIGIN = 'https://piblo.co.uk';

export default async function handler(req, res, fetchImpl = fetch) {
  res.setHeader('cache-control', 'no-store, max-age=0');
  res.setHeader('x-content-type-options', 'nosniff');
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.statusCode = 405;
    res.setHeader('allow', 'GET, HEAD');
    return res.end();
  }
  // HEAD (link previews, uptime pings) must never create a session.
  if (req.method === 'HEAD') {
    res.statusCode = 204;
    return res.end();
  }
  const key = process.env.STRIPE_SECRET_KEY || '';
  const price = process.env.STRIPE_PRICE_ID || '';
  if (!key || !price) return fail(res, 'unconfigured');

  const body = new URLSearchParams({
    mode: 'payment',
    'line_items[0][price]': price,
    'line_items[0][quantity]': '1',
    'payment_intent_data[description]': 'Piblo Standard batch one reservation (refundable)',
    'metadata[product]': 'piblo-standard-batch-one-reservation',
    submit_type: 'pay',
    success_url: `${ORIGIN}/reserved/?session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: `${ORIGIN}/#choose`,
  });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetchImpl(SESSIONS_URL, {
      method: 'POST',
      headers: { authorization: `Bearer ${key}`, 'content-type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
      signal: controller.signal,
    });
    const session = await response.json().catch(() => ({}));
    // Only ever redirect to Stripe's own hosted checkout.
    if (!response.ok || !/^https:\/\/checkout\.stripe\.com\//.test(session.url || '')) return fail(res, 'stripe');
    res.statusCode = 303;
    res.setHeader('location', session.url);
    return res.end();
  } catch {
    return fail(res, 'network');
  } finally {
    clearTimeout(timer);
  }
}

// Never show a raw error: send the visitor back to the page with a friendly flag.
function fail(res, reason) {
  console.error(`reserve: ${reason}`);
  res.statusCode = 303;
  res.setHeader('location', '/?reserve=failed#choose');
  return res.end();
}
