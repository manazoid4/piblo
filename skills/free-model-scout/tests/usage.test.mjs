import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { PROVIDERS, fromOpenRouter, fromGroqHeaders, check, readOnlyStatus } from "../usage.mjs";

test("OpenRouter true 1000 RPD account allowance is read from current key, not guessed", () => {
  const s = fromOpenRouter({ data: { is_free_tier: false,
    free_model_daily_requests: { limit: 1000, remaining: 843, used: 157 },
    limit_remaining: 2.5 } });
  assert.equal(s.free_model_daily_requests.limit, 1000);
  assert.equal(s.free_model_daily_requests.remaining, 843);
  assert.equal(s.free_model_daily_requests.verified, true);
  assert.equal(s.paid_key_limit_remaining_usd, 2.5);
  assert.equal(s.rate_per_minute, null); // key endpoint does NOT provide RPM
});
test("OpenRouter missing/inconsistent quota fails closed", () => {
  assert.equal(fromOpenRouter({ data: {} }).free_model_daily_requests.verified, false);
  assert.equal(fromOpenRouter({ data: { free_model_daily_requests: { limit: 50, remaining: 52 } } }).free_model_daily_requests.verified, false);
  assert.equal(fromOpenRouter({ data: { free_model_daily_requests: { limit: -3, remaining: 4 } } }).free_model_daily_requests.limit, null);
});
test("Groq headers parse real request and token windows independently", () => {
  const r = fromGroqHeaders({
    "X-RateLimit-Limit-Requests": "1000",
    "x-ratelimit-remaining-requests": "999",
    "x-ratelimit-limit-tokens": "8000",
    "X-RateLimit-Remaining-Tokens": "7900",
    "x-ratelimit-reset-requests": "4h",
    "x-ratelimit-reset-tokens": "10s",
  });
  assert.equal(r.requests.window, "daily");
  assert.equal(r.requests.remaining, 999);
  assert.equal(r.tokens.remaining, 7900);
  assert.equal(r.tokens.window, "per-minute");
  assert.equal(r.requests.verified, true);
});
test("unknown Groq quota stays unknown", () => {
  const s = fromGroqHeaders({});
  assert.equal(s.requests.verified, false);
  assert.equal(s.tokens.remaining, null);
});
test("Nous and NVIDIA are not magically certified free", () => {
  for (const p of ["nous", "nvidia"]) {
    assert.equal(readOnlyStatus(p, { configured: true }).free_status, "account-entitlement-unverified");
    assert.equal(readOnlyStatus(p, { configured: true }).remaining_requests, null);
  }
});
test("no key means no network request", async () => {
  let calls = 0;
  const result = await check("openrouter", {
    env: {},
    fetchImpl: async () => { calls += 1; throw Error("should not fetch"); },
  });
  assert.equal(calls, 0);
  assert.equal(result.configured, false);
});
test("quota check never sends an inference POST", async () => {
  const methods = [];
  const data = { data: { free_model_daily_requests: { limit: 1000, used: 0, remaining: 1000 } } };
  const result = await check("openrouter", { env: { OPENROUTER_API_KEY: "test-placeholder" },
    fetchImpl: async (url, cfg) => {
      assert.equal(url, PROVIDERS.openrouter.url);
      methods.push(cfg.method);
      return { ok: true, headers: new Headers(), text: async () => JSON.stringify(data) };
    },
  });
  assert.deepEqual(methods, ["GET"]);
  assert.equal(result.free_model_daily_requests.remaining, 1000);
});
test("header 429 treated as no readiness with retry hint", async () => {
  const result = await check("groq", { env: { GROQ_API_KEY: "test-placeholder" },
    fetchImpl: async () => ({ ok: false, status: 429, headers: new Headers({ "Retry-After": "45" }) }),
  });
  assert.equal(result.reachable, false);
  assert.equal(result.retry_after, "45");
});
test("CLI fixture works without secrets, and no $ tier is inferred", () => {
  const dir = fileURLToPath(new URL("../", import.meta.url));
  const p = join(dir, "tests", "openrouter-usage-fixture.json");
  const v = spawnSync(process.execPath, [join(dir,"usage.mjs"),"--provider=openrouter","--fixture="+p,"--json"],
    {encoding:"utf8",timeout:5000,env:{}});
  assert.equal(v.status,0,v.stderr);
  const body=JSON.parse(v.stdout);
  assert.equal(body.statuses[0].free_model_daily_requests.remaining, 863);
  assert.equal(body.statuses[0].data_type, "test-fixture");
});
