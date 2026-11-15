import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { eligible, normalize, rank, verifiedZero } from "../scout.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const fixture = join(root, "tests", "openrouter-fixture.json");
const fake = [
  { id: "a/coder:free", context_length: 131072, pricing: { prompt: "0", completion: "0.000" }, supported_parameters: ["tools"] },
  { id: "b/expensive", context_length: 262144, pricing: { prompt: "0.001", completion: "0.003" } },
  { id: "c/free-missing-price:free" },
  { id: "d/free-fake:free", pricing: { prompt: "0", completion: "0.10" } },
];
test("free zero-price classifier fails closed", () => {
  assert.equal(verifiedZero(fake[0]), true);
  for (const row of fake.slice(1)) assert.equal(!!verifiedZero(row), false);
});
test("paid/unverified default cannot be accidentally selected", () => {
  const models = normalize("openrouter", { data: fake });
  assert.deepEqual(eligible(models).map(x => x.id), ["a/coder:free"]);
  assert.equal(eligible(models, { includeUnverified: true }).length, 1);
});
test("OpenRouter paid choices never become eligible under include-unverified", () => {
  const models = normalize("openrouter", { data: fake });
  assert.ok(models.slice(1).every(x => x.status === "paid-or-unknown"));
  assert.deepEqual(eligible(models, { includeUnverified: true }).map(x => x.id), ["a/coder:free"]);
});
test("Groq and NVIDIA catalog cannot claim user has a free account", () => {
  for (const provider of ["groq", "nvidia"]) {
    const entries = normalize(provider, { data: [{ id: "fake/model", context_length: 8192 }] });
    assert.equal(entries[0].status, "unverified-account-tier");
    assert.deepEqual(eligible(entries), []);
  }
});
test("local Ollama is tagged unmetered but not magically free hardware", () => {
  const m = normalize("ollama", { models: [{ name: "local:q4", size: 100 }] })[0];
  assert.equal(m.status, "local-unmetered");
  assert.equal(m.size_bytes, 100);
  assert.ok(eligible([m]).length);
});
test("task heuristics are deterministic and explicit", () => {
  const m = normalize("openrouter", { data: fake })[0];
  assert.ok(rank(m, "code") > rank(m, "quick"));
  assert.ok(rank(m, "tools") > rank(m, "vision"));
});
test("CLI reads fixture, returns machine readable data, no network", () => {
  const r = spawnSync(process.execPath, [join(root, "scout.mjs"), "--provider=openrouter", "--fixture=" + fixture, "--task=code", "--json"], { encoding: "utf8", timeout: 8000 });
  assert.equal(r.status, 0, r.stderr);
  const data = JSON.parse(r.stdout);
  assert.equal(data.models.length, 1);
  assert.equal(data.models[0].id, "a/coder:free");
  assert.equal(data.models[0].fetched, "fixture");
});
test("probe defaults OFF, fixtures cannot turn it on, paid choice rejected", () => {
  const r = spawnSync(process.execPath, [join(root, "scout.mjs"), "--provider=openrouter", "--fixture=" + fixture, "--probe=a/coder:free", "--confirm-free-probe"], { encoding: "utf8", timeout: 8000 });
  assert.equal(r.status, 1);
  assert.match(r.stderr, /probe needs/);
});
