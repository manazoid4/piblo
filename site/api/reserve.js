// £5 refundable reservation, taken off the £129 early price, for Piblo Standard (batch one). Redirects to a Stripe Payment Link,
// so no secret key lives on this site. GET on purpose: a plain link works without JavaScript.
const PAYMENT_LINK = 'https://buy.stripe.com/4gMbIT2TGfeNb128eIabK01';

export default function handler(req, res) {
  res.setHeader('cache-control', 'no-store, max-age=0');
  res.setHeader('x-content-type-options', 'nosniff');
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.statusCode = 405;
    res.setHeader('allow', 'GET, HEAD');
    return res.end();
  }
  res.statusCode = 303;
  res.setHeader('location', PAYMENT_LINK);
  return res.end();
}
