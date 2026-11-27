import test from "node:test";
import assert from "node:assert/strict";
import { createCipheriv } from "node:crypto";
import handler, { aesCmac, decryptPicc, sunMac, diversify, metaKey, verifyTap } from "../api/key.js";

const zero = Buffer.alloc(16);

test("AES-CMAC RFC 4493 vectors", () => {
  const key = Buffer.from("2b7e151628aed2a6abf7158809cf4f3c", "hex");
  assert.equal(aesCmac(key, Buffer.alloc(0)).toString("hex"), "bb1d6929e95937287fa37d129b756746");
  assert.equal(aesCmac(key, Buffer.from("6bc1bee22e409f96e93d7e117393172a", "hex")).toString("hex"), "070a16b46b4d4144f79bdd9dd04a287c");
});

test("NXP AN12196 SUN vector (zero keys)", () => {
  const picc = decryptPicc(zero, Buffer.from("EF963FF7828658A599F3041510671E88", "hex"));
  assert.equal(picc.uid.toString("hex"), "04de5f1eacc040");
  assert.equal(picc.counter, 61);
  assert.equal(sunMac(zero, picc.uid, picc.counter).toString("hex"), "94eed9ee65337086");
});

function tap(master, uid, counter) {
  const plain = Buffer.concat([Buffer.from([0xc7]), uid, Buffer.from([counter, 0, 0]), Buffer.alloc(5)]);
  const cipher = createCipheriv("aes-128-cbc", metaKey(master), zero);
  cipher.setAutoPadding(false);
  const p = Buffer.concat([cipher.update(plain), cipher.final()]);
  return { p, c: sunMac(diversify(master, uid, 2), uid, counter) };
}

test("diversified tap verifies, tampering does not", () => {
  const master = Buffer.from("000102030405060708090a0b0c0d0e0f", "hex");
  const uid = Buffer.from("04aabbccddee01", "hex");
  const { p, c } = tap(master, uid, 9);
  assert.equal(verifyTap(p, c, master).genuine, true);
  assert.equal(verifyTap(p, Buffer.alloc(8), master).genuine, false);
  assert.equal(verifyTap(p, c, Buffer.alloc(16)).genuine, false);
});

function call(url, method = "GET") {
  const res = { headers: {}, setHeader(k, v) { this.headers[k] = v; }, end(b) { this.body = b; } };
  handler({ url, method }, res);
  return res;
}

test("handler: unconfigured, invalid, genuine", () => {
  delete process.env.PIBLO_KEY_MASTER;
  assert.equal(call("/api/key?p=" + "0".repeat(32) + "&c=" + "0".repeat(16)).statusCode, 503);
  process.env.PIBLO_KEY_MASTER = "000102030405060708090a0b0c0d0e0f";
  assert.equal(call("/api/key?p=zz&c=00").statusCode, 400);
  assert.equal(call("/api/key", "POST").statusCode, 405);
  const master = Buffer.from(process.env.PIBLO_KEY_MASTER, "hex");
  const uid = Buffer.from("04aabbccddee01", "hex");
  const { p, c } = tap(master, uid, 3);
  const ok = call(`/api/key?p=${p.toString("hex")}&c=${c.toString("hex")}`);
  assert.deepEqual(JSON.parse(ok.body), { ok: true, genuine: true, uid_tail: "EE01" });
  // A different card's MAC under the same meta key must fail (per-card file key).
  const other = tap(master, Buffer.from("04aabbccddee02", "hex"), 3);
  assert.equal(JSON.parse(call(`/api/key?p=${p.toString("hex")}&c=${other.c.toString("hex")}`).body).genuine, false);
  delete process.env.PIBLO_KEY_MASTER;
});
