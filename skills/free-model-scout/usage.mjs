#!/usr/bin/env node
// Read-only quota discovery for Piblo Free Model Scout. Never performs inference.
import { parseArgs } from "node:util";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

export const PROVIDERS = Object.freeze({
  openrouter: { env: "OPENROUTER_API_KEY", url: "https://openrouter.ai/api/v1/key" },
  groq: { env: "GROQ_API_KEY", url: "https://api.groq.com/openai/v1/models" },
  nvidia: { env: "NVIDIA_API_KEY", url: "https://integrate.api.nvidia.com/v1/models" },
  nous: { env: "NOUS_API_KEY", url: null }, // Exact Nous Cloud endpoint/account tier must be verified
  ollama: { env: null, url: "http://127.0.0.1:11434/api/tags" },
});
const MAX_READ = 2 * 1024 * 1024; // read-only catalogs can be large

function quantity(value) {
  const n = Number(value);
  return value !== undefined && value !== null && value !== "" && Number.isSafeInteger(n) && n >= 0 ? n : null;
}
function budget(value) {
  const n = Number(value);
  return value !== undefined && value !== null && value !== "" && Number.isFinite(n) && n >= 0 ? n : null;
}
export function fromOpenRouter(payload) {
  const data = payload?.data;
  const free = data?.free_model_daily_requests;
  const limit = quantity(free?.limit), remaining = quantity(free?.remaining), used = quantity(free?.used);
  return {
    provider: "openrouter", configured: true,
    free_model_daily_requests: {
      limit, remaining, used,
      verified: limit !== null && remaining !== null && remaining <= limit,
    },
    // This optional field is key-level paid spending headroom, NOT a 'free tier' balance.
    paid_key_limit_remaining_usd: budget(data?.limit_remaining),
    account_tier: "from-key-endpoint", rate_per_minute: null,
    quota_source: "https://openrouter.ai/api/v1/key",
  };
}
function hget(headers, key) {
  if (!headers) return null;
  const value = headers instanceof Headers ? headers.get(key) :
    Object.entries(headers).find(([k]) => k.toLowerCase() === key.toLowerCase())?.[1];
  return typeof value === "string" ? value : null;
}
export function fromGroqHeaders(headers) {
  const requests = {
    limit: quantity(hget(headers, "x-ratelimit-limit-requests")),
    remaining: quantity(hget(headers, "x-ratelimit-remaining-requests")),
    resets_in: hget(headers, "x-ratelimit-reset-requests"),
    window: "daily",
  };
  const tokens = {
    limit: quantity(hget(headers, "x-ratelimit-limit-tokens")),
    remaining: quantity(hget(headers, "x-ratelimit-remaining-tokens")),
    resets_in: hget(headers, "x-ratelimit-reset-tokens"),
    window: "per-minute",
  };
  return {
    provider: "groq",
    requests: { ...requests, verified: requests.limit !== null && requests.remaining !== null && requests.remaining <= requests.limit },
    tokens: { ...tokens, verified: tokens.limit !== null && tokens.remaining !== null && tokens.remaining <= tokens.limit },
    retry_after: hget(headers, "retry-after"),
    quota_source: "observed rate-limit headers; not an account-wide GET quota endpoint",
  };
}
export function readOnlyStatus(provider, detail = {}) {
  if (!PROVIDERS[provider]) throw Error("unsupported provider");
  if (provider === "ollama") return {
    provider, configured: true, reachable: !!detail.reachable,
    free_status: "local-unmetered", remaining_requests: null,
    quota_source: "local inference, limited by hardware/resources",
  };
  return {
    provider, configured: !!detail.configured, reachable: !!detail.reachable,
    free_status: provider === "openrouter" ? "catalog-and-key-must-be-checked" : "account-entitlement-unverified",
    remaining_requests: null, quota_source: "unknown: provider account/dashboard or response headers",
  };
}
export async function check(provider, { fetchImpl = fetch, timeoutMs = 7000, env = process.env } = {}) {
  const p = PROVIDERS[provider];
  if (!p) throw Error("unsupported provider");
  if (p.env && !env[p.env]) return { ...readOnlyStatus(provider), reason: "API key env var not set" };
  if (!p.url) return { ...readOnlyStatus(provider, { configured: true }), reason: "provider account API URL not verified; no network request attempted" };
  const controller = AbortSignal.timeout(timeoutMs);
  try {
    const response = await fetchImpl(p.url, {
      method: "GET", signal: controller, headers: {
        Accept: "application/json",
        ...(p.env ? { Authorization: "Bearer " + env[p.env] } : {}),
      },
    });
    if (!response.ok) return {
      ...readOnlyStatus(provider, { configured: true }),
      reason: "HTTP " + response.status,
      retry_after: response.headers?.get?.("retry-after") ?? null,
    };
    const size = Number(response.headers?.get?.("content-length"));
    if (Number.isFinite(size) && size > MAX_READ) return { ...readOnlyStatus(provider, { configured: true }), reason: "large response" };
    const text = await response.text();
    if (text.length > MAX_READ) return { ...readOnlyStatus(provider, { configured: true }), reason: "large response" };
    const body = JSON.parse(text);
    const base = readOnlyStatus(provider, { configured: true, reachable: true });
    if (provider === "openrouter") return {
      ...base, ...fromOpenRouter(body), checked_at: new Date().toISOString(),
    };
    if (provider === "groq") return {
      ...base, ...fromGroqHeaders(response.headers),
      // The /models response might not carry rate headers. Unknown MUST remain unknown.
      checked_at: new Date().toISOString(),
    };
    return { ...base, checked_at: new Date().toISOString() };
  } catch (error) {
    // Never include raw response bodies, URLs with keys, token fragments, or auth headers.
    return { ...readOnlyStatus(provider, { configured: true }), reason: error.name === "TimeoutError" ? "timeout" : "read-only status unavailable" };
  }
}
async function main() {
  const { values } = parseArgs({ options: {
    provider: { type: "string", default: "all" }, json: { type: "boolean", default: false },
    fixture: { type: "string" }, "groq-headers": { type: "string" },
    timeout: { type: "string", default: "7000" },
  }});
  const timeoutMs = Number(values.timeout);
  if (!Number.isInteger(timeoutMs) || timeoutMs < 500 || timeoutMs > 30000) throw Error("timeout must be 500-30000ms");
  const providers = values.provider === "all" ? Object.keys(PROVIDERS) : [values.provider];
  if (providers.some(p => !PROVIDERS[p])) throw Error("unknown provider");
  if (values.fixture && (providers.length !== 1 || values["groq-headers"])) throw Error("--fixture requires one provider");
  if (values["groq-headers"] && (providers.length !== 1 || providers[0] !== "groq")) throw Error("--groq-headers requires Groq only");
  const statuses = [];
  for (const provider of providers) {
    if (values.fixture) {
      const body = JSON.parse(await readFile(values.fixture, "utf8"));
      const v = provider === "openrouter" ? fromOpenRouter(body) : readOnlyStatus(provider, { configured: true, reachable: true });
      statuses.push({ ...v, data_type: "test-fixture", checked_at: new Date().toISOString() });
    } else if (values["groq-headers"]) {
      const headers = JSON.parse(await readFile(values["groq-headers"], "utf8"));
      statuses.push({ ...fromGroqHeaders(headers), data_type: "user-supplied-headers", checked_at: new Date().toISOString() });
    } else {
      statuses.push(await check(provider, { timeoutMs }));
    }
  }
  const result = { schema_version: 1, checked_at: new Date().toISOString(),
    statuses, warnings: [
      "Read-only checks never send an inference request or verify model quality.",
      "Account/provider free tiers and usage limits are distinct. Unknown means unknown.",
      "Never auto-route private data or invoke paid models without separate informed authorization.",
    ],
  };
  if (values.json) console.log(JSON.stringify(result, null, 2));
  else for (const status of statuses) console.log(status.provider + ": " + JSON.stringify(status));
}
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1]))
  main().catch(err => { console.error("Quota check: " + String(err.message).slice(0, 150)); process.exitCode = 1; });
