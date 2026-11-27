// Public "is this a genuine Piblo Key?" check (NTAG 424 DNA SUN, NXP AN12196). Plain Node crypto, no dependencies.
// PIBLO_KEY_MASTER (32 hex) lives only in Vercel env. No counter is stored here, so the answer is "genuine",
// never "approved": replay protection and approvals happen in Piblo Core, not on the site.
import { createCipheriv, createDecipheriv, timingSafeEqual } from 'node:crypto';

const HEX = /^[0-9a-fA-F]+$/;
const TAG = Buffer.from('piblo-key-v1');
const ZERO_IV = Buffer.alloc(16);

function aesEcb(key, block) {
  const cipher = createCipheriv('aes-128-ecb', key, null);
  cipher.setAutoPadding(false);
  return Buffer.concat([cipher.update(block), cipher.final()]);
}

function xor(a, b) {
  return Buffer.from(a.map((byte, i) => byte ^ b[i]));
}

function shiftLeft(block) {
  const out = Buffer.alloc(16);
  for (let i = 0; i < 16; i += 1) out[i] = ((block[i] << 1) | (i < 15 ? block[i + 1] >> 7 : 0)) & 0xff;
  return out;
}

// AES-CMAC (RFC 4493)
export function aesCmac(key, message) {
  const l = aesEcb(key, Buffer.alloc(16));
  const k1 = shiftLeft(l);
  if (l[0] & 0x80) k1[15] ^= 0x87;
  const k2 = shiftLeft(k1);
  if (k1[0] & 0x80) k2[15] ^= 0x87;
  const blocks = Math.max(1, Math.ceil(message.length / 16));
  const complete = message.length > 0 && message.length % 16 === 0;
  let x = Buffer.alloc(16);
  for (let i = 0; i < blocks - 1; i += 1) x = aesEcb(key, xor(x, message.subarray(i * 16, i * 16 + 16)));
  const last = Buffer.alloc(16);
  const tail = message.subarray((blocks - 1) * 16);
  tail.copy(last);
  if (complete) return aesEcb(key, xor(xor(x, last), k1));
  last[tail.length] = 0x80;
  return aesEcb(key, xor(xor(x, last), k2));
}

export function diversify(master, uid, role) {
  return aesCmac(master, Buffer.concat([Buffer.from([role]), uid, TAG]));
}

/** Shared SDM meta-read key: the UID is inside the encrypted PICCData, so it can't be per card. */
export function metaKey(master) {
  return aesCmac(master, Buffer.concat([Buffer.from([0x01]), Buffer.alloc(7), TAG]));
}

export function decryptPicc(kMeta, p) {
  const decipher = createDecipheriv('aes-128-cbc', kMeta, ZERO_IV);
  decipher.setAutoPadding(false);
  const plain = Buffer.concat([decipher.update(p), decipher.final()]);
  if (plain[0] !== 0xc7) return null;
  return { uid: plain.subarray(1, 8), counter: plain.readUIntLE(8, 3) };
}

export function sunMac(kFile, uid, counter) {
  const ctr = Buffer.alloc(3);
  ctr.writeUIntLE(counter, 0, 3);
  const session = aesCmac(kFile, Buffer.concat([Buffer.from('3cc300010080', 'hex'), uid, ctr]));
  const full = aesCmac(session, Buffer.alloc(0));
  return Buffer.from([1, 3, 5, 7, 9, 11, 13, 15].map((i) => full[i]));
}

/**
 * Check a tap. Decrypts PICCData with the shared meta key to learn the UID, then checks the MAC with
 * that card's own file key. Returns {genuine, uidTail}. No counter storage here, so "genuine" only.
 */
export function verifyTap(p, c, master) {
  const picc = decryptPicc(metaKey(master), p);
  if (!picc) return { genuine: false };
  const want = sunMac(diversify(master, picc.uid, 0x02), picc.uid, picc.counter);
  const genuine = want.length === c.length && timingSafeEqual(want, c);
  return genuine ? { genuine, uidTail: picc.uid.toString('hex').slice(-4).toUpperCase() } : { genuine: false };
}

function reply(res, status, body) {
  res.statusCode = status;
  res.setHeader('cache-control', 'no-store, max-age=0');
  res.setHeader('x-content-type-options', 'nosniff');
  res.setHeader('content-type', 'application/json; charset=utf-8');
  return res.end(JSON.stringify(body));
}

export default function handler(req, res) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.statusCode = 405;
    res.setHeader('allow', 'GET, HEAD');
    return res.end();
  }
  const master = Buffer.from(process.env.PIBLO_KEY_MASTER || '', 'hex');
  if (master.length !== 16) return reply(res, 503, { ok: false, reason: 'unconfigured' });
  const query = new URL(req.url, 'https://piblo.co.uk').searchParams;
  const p = query.get('p') || '';
  const c = query.get('c') || '';
  if (!(p.length === 32 && c.length === 16 && HEX.test(p) && HEX.test(c))) return reply(res, 400, { ok: false, reason: 'invalid' });
  const result = verifyTap(Buffer.from(p, 'hex'), Buffer.from(c, 'hex'), master);
  return reply(res, 200, { ok: true, genuine: result.genuine, ...(result.uidTail && { uid_tail: result.uidTail }) });
}
