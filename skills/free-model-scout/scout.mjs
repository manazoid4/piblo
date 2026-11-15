#!/usr/bin/env node
// Piblo Free Model Scout: no dependencies, no paid-model fallback, no hidden probing.
import { parseArgs } from "node:util";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";

const SOURCES = Object.freeze({
  openrouter: { url: "https://openrouter.ai/api/v1/models", env: "OPENROUTER_API_KEY" },
  groq: { url: "https://api.groq.com/openai/v1/models", env: "GROQ_API_KEY" },
  nvidia: { url: "https://integrate.api.nvidia.com/v1/models", env: "NVIDIA_API_KEY" },
  ollama: { url: "http://127.0.0.1:11434/api/tags", env: null },
});
const CACHE_DIR = process.env.PIBLO_SCOUT_CACHE || join(homedir(), ".cache", "piblo-free-model-scout");
const CACHE_MS = 5 * 60 * 1000;
const MAX_BODY_BYTES = 2_000_000;

export const verifiedZero = (model) =>
  typeof model?.id === "string" &&
  model.id.endsWith(":free") &&
  model.pricing &&
  ["prompt", "completion"].every((key) =>
    model.pricing[key] !== undefined &&
    model.pricing[key] !== null &&
    model.pricing[key] !== "" &&
    Number.isFinite(Number(model.pricing[key])) &&
    Number(model.pricing[key]) === 0);

export function normalize(provider, data) {
  if (provider === "ollama") {
    return (Array.isArray(data?.models) ? data.models : []).filter(x => typeof x.name === "string")
      .map(x => ({
        provider, id: x.name, context: null, input: ["text"], status: "local-unmetered",
        source: "local Ollama", size_bytes: x.size || 0, evidence: "local model; hardware costs apply",
      }));
  }
  const models = Array.isArray(data?.data) ? data.data : [];
  return models.filter(x => typeof x.id === "string").map(x => {
    const free = provider === "openrouter" && verifiedZero(x);
    const input = x.architecture?.input_modalities || ["text"];
    return {
      provider, id: x.id,
      context: Number.isFinite(Number(x.context_length)) ? Number(x.context_length) : null,
      input: Array.isArray(input) ? input : ["text"],
      status: free ? "catalog-zero-cost" : (provider === "openrouter" ? "paid-or-unknown" : "unverified-account-tier"),
      source: SOURCES[provider].url,
      evidence: free ? "live :free suffix AND $0 prompt/completion catalog prices"
        : "provider model list alone does not verify account free-tier entitlement",
      tool_hint: Array.isArray(x.supported_parameters) && x.supported_parameters.includes("tools"),
    };
  });
}
export function rank(model, task) {
  const id = model.id.toLowerCase();
  let score = 0;
  if (task === "code") score += /code|coder|devstral|qwen|gpt-oss|nemotron/.test(id) ? 30 : 0;
  if (task === "reasoning") score += /reason|think|qwq|nemotron|gpt-oss|deepseek/.test(id) ? 30 : 0;
  if (task === "vision") score += model.input.some(x => /image/i.test(x)) ? 40 : -100;
  if (task === "quick") score += /flash|mini|nano|small|lightning|instant/.test(id) ? 30 : 0;
  if (task === "tools") score += model.tool_hint ? 40 : -10;
  score += Math.min(30, Math.floor(Math.log2(Math.max(1, model.context || 1)) * 1.5));
  score += model.status === "local-unmetered" ? 5 : 0;
  return score; // Discovery heuristic, not a measured benchmark or proof of quality.
}
async function requestJson(url, key, timeoutMs) {
  const headers = { Accept: "application/json" };
  if (key) headers.Authorization = "Bearer " + key;
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(timeoutMs) });
  if (!response.ok) throw Error("HTTP " + response.status + " (no retry)");
  if (Number(response.headers.get("content-length")) > MAX_BODY_BYTES) throw Error("catalog too large");
  const txt = await response.text();
  if (txt.length > MAX_BODY_BYTES) throw Error("catalog too large");
  return JSON.parse(txt);
}
export async function discover(provider, { offline = false, fixture = null, timeoutMs = 7000 } = {}) {
  if (!SOURCES[provider]) throw Error("unknown provider");
  const path = join(CACHE_DIR, provider + ".json");
  if (fixture) {
    const data = JSON.parse(await readFile(fixture, "utf8"));
    return { models: normalize(provider, data), fetched: "fixture", stale: false };
  }
  try {
    const old = JSON.parse(await readFile(path, "utf8"));
    if (offline || Date.now() - old.checked_at < CACHE_MS)
      return { models: normalize(provider, old.data), fetched: "cache", stale: offline || false };
  } catch { if (offline) throw Error("no offline catalog for " + provider); }
  if (offline) throw Error("no cached catalog");
  const env = SOURCES[provider].env;
  if (provider !== "openrouter" && env && !process.env[env])
    throw Error(provider + " catalog requires " + env + "; skip, do not guess free entitlement");
  const data = await requestJson(SOURCES[provider].url, env ? process.env[env] : null, timeoutMs);
  await mkdir(CACHE_DIR, { recursive: true, mode: 0o700 });
  await writeFile(path, JSON.stringify({ checked_at: Date.now(), data }), { mode: 0o600 });
  return { models: normalize(provider, data), fetched: "live", stale: false };
}
export function eligible(models, { includeUnverified = false } = {}) {
  return models.filter(x =>
    x.status === "catalog-zero-cost" || x.status === "local-unmetered" ||
    (includeUnverified && x.status === "unverified-account-tier"));
}
const TESTS = {
  quick: { prompt: "Reply with exactly PING and nothing else.", expected: "PING" },
  reasoning: { prompt: "Compute 17 multiplied by 19. Reply with only the integer.", expected: "323" },
  code: { prompt: "Reply with only the integer result of 17 * 19. Do not explain.", expected: "323" },
  tools: { prompt: "Reply with exactly PING and nothing else.", expected: "PING" },
};
async function probe(model, task, timeoutMs) {
  if (model.provider !== "openrouter" || model.status !== "catalog-zero-cost")
    throw Error("online probes only support live-verified OpenRouter :free models");
  if (!process.env.OPENROUTER_API_KEY) throw Error("OPENROUTER_API_KEY is required for opt-in probe");
  const t = TESTS[task] || TESTS.quick;
  const start = performance.now();
  const response = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    signal: AbortSignal.timeout(timeoutMs),
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + process.env.OPENROUTER_API_KEY },
    body: JSON.stringify({ model: model.id, messages: [{ role: "user", content: t.prompt }], max_tokens: 64 }),
  });
  if (!response.ok) throw Error("probe HTTP " + response.status + " (no retry; stop on 402/429)");
  const json = await response.json();
  const output = String(json?.choices?.[0]?.message?.content || "").trim();
  return {
    provider: model.provider, id: model.id, task, success: output === t.expected,
    latency_ms: Math.round(performance.now() - start), input_tokens: json.usage?.prompt_tokens ?? null,
    output_tokens: json.usage?.completion_tokens ?? null, at: new Date().toISOString(),
    // Never log prompt, output or API key; model quality needs more rigorous benchmarks.
  };
}
async function main() {
  const { values } = parseArgs({ options: {
    provider: { type: "string", default: "all" }, task: { type: "string", default: "code" },
    limit: { type: "string", default: "8" }, json: { type: "boolean", default: false },
    offline: { type: "boolean", default: false }, fixture: { type: "string" },
    "include-unverified": { type: "boolean", default: false },
    probe: { type: "string" }, "confirm-free-probe": { type: "boolean", default: false },
    timeout: { type: "string", default: "7000" },
  }, strict: true });
  if (!["code", "reasoning", "quick", "vision", "tools"].includes(values.task))
    throw Error("task must be code, reasoning, quick, vision or tools");
  const limit = Number(values.limit), timeout = Number(values.timeout);
  if (!Number.isInteger(limit) || limit < 1 || limit > 30) throw Error("limit 1-30");
  if (!Number.isInteger(timeout) || timeout < 500 || timeout > 30000) throw Error("timeout 500-30000ms");
  const providers = values.provider === "all" ? Object.keys(SOURCES) : [values.provider];
  if (values.fixture && providers.length !== 1) throw Error("--fixture needs one --provider");
  if (values.probe && (providers.length !== 1 || values.provider !== "openrouter"))
    throw Error("--probe requires --provider=openrouter");
  const output = { task: values.task, models: [], errors: [], notes: [
    "Rank is a transparent metadata heuristic, not an independent quality benchmark.",
    "Free API tiers and quotas can change; never fall back to a paid model.",
    "Never send private code/customer data to a free endpoint without informed consent.",
  ] };
  for (const provider of providers) {
    try {
      const v = await discover(provider, { offline: values.offline, fixture: values.fixture, timeoutMs: timeout });
      for (const m of eligible(v.models, { includeUnverified: values["include-unverified"] }))
        output.models.push({ ...m, fetched: v.fetched, stale: v.stale, heuristic_score: rank(m, values.task) });
    } catch (err) {
      output.errors.push({ provider, message: String(err.message).slice(0, 160) });
    }
  }
  output.models.sort((a, b) => b.heuristic_score - a.heuristic_score || a.id.localeCompare(b.id));
  output.models = output.models.slice(0, limit);
  if (values.probe) {
    if (!values["confirm-free-probe"] || values.offline || values.fixture)
      throw Error("probe needs --confirm-free-probe, network and a live price check");
    // Freshly revalidate prices and eligibility; do not trust a cached or stale model.
    const raw = await requestJson(SOURCES.openrouter.url, process.env.OPENROUTER_API_KEY, timeout);
    const match = eligible(normalize("openrouter", raw)).find(m => m.id === values.probe);
    if (!match) throw Error("model is not currently certified catalog-zero-cost");
    output.probe = await probe(match, values.task, timeout);
    // No automatic multi-model fan-out, no hidden retries, no provider-key rotation.
  }
  console.log(values.json ? JSON.stringify(output, null, 2) :
    output.models.map(m => [m.provider, m.id, m.status, "score=" + m.heuristic_score, m.stale ? "STALE" : ""].join("  ")).join("\n") +
    (output.errors.length ? "\nErrors: " + JSON.stringify(output.errors) : "") +
    (output.probe ? "\nProbe: " + JSON.stringify(output.probe) : ""));
}
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch(err => { console.error("Scout error: " + String(err.message).slice(0, 200)); process.exitCode = 1; });
}
