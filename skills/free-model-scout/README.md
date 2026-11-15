# Piblo Free Model Scout

**A free, open-source, no-dependency, safety-first AI model discovery skill for coding agents.** The scripts and skill in this folder are MIT licensed, separately from the parent Piblo repository's PolyForm Noncommercial licence (see local `LICENSE`). It can be copied and used commercially under this directory's MIT licence; other Piblo source files retain their own licences.

## What it actually does
- Queries the **live OpenRouter catalog**, selecting only model IDs ending `:free` **and** containing $0 prompt and completion token prices. This is a catalog guarantee, not a promise the API request succeeds or that free quota remains.
- Checks local Ollama on `127.0.0.1` for no-provider-bill models.
- Optionally lists Groq and NVIDIA model IDs using the caller's key, **flagging those account free-tier terms as unverified**. Their `/models` endpoints do not establish zero price.
- Sorts candidates using a transparent task heuristic (`code`, `reasoning`, `quick`, `tools`, `vision`). This is not an independent quality leaderboard. An optional **single** OpenRouter free model check measures a real round-trip and one simple correctness test.
- Uses a five-minute on-disk model-list cache (no secrets). Does not install packages, run received model code, make inference calls on discovery, rotate keys, scrape account pages or spend paid tokens.

## Quick start
Requires Node.js 20+.

```bash
cd skills/free-model-scout
node scout.mjs --provider=openrouter --task=code --json
node scout.mjs --provider=ollama --task=quick
node scout.mjs --provider=all --task=reasoning --json
# Strictly optional single network completion:
export OPENROUTER_API_KEY="set-this-in-your-own-shell-or-secret-manager"
node scout.mjs --provider=openrouter --task=quick --probe="PROVIDER/MODEL:free" --confirm-free-probe --json
# Authenticated catalog discovery, not zero-cost verification:
node scout.mjs --provider=groq --include-unverified --json
node scout.mjs --provider=nvidia --include-unverified --json
```

On PowerShell set the env variable only for the current session: `$env:OPENROUTER_API_KEY="..."`. Do not paste secrets into GitHub, command arguments, AI chat, screenshots or logs. For a real product, use the supported OS credential store.

Output: `status=catalog-zero-cost`, `local-unmetered` or `unverified-account-tier`; `heuristic_score`; `source`; and errors (e.g., rate limit, no local Ollama). By default only verified-zero-price and local models are listed.

## Check account quotas and model access without generating tokens

A separate **read-only** command inspects the currently configured providers; it does not call chat completions or claim API listings are all free:

```bash
node skills/free-model-scout/usage.mjs --provider=openrouter --json
node skills/free-model-scout/usage.mjs --provider=groq --json
node skills/free-model-scout/usage.mjs --provider=nvidia --json
node skills/free-model-scout/usage.mjs --provider=nous --json
node skills/free-model-scout/usage.mjs --provider=all --json
```

For OpenRouter, `GET /api/v1/key` may provide `free_model_daily_requests` with `limit`/`remaining`/`used`. This is **per key**, if returned; the code does not infer an entitlement from a deposit claim. OpenRouter `:free` is normally 20 RPM and either 50/day or 1,000/day after qualifying lifetime credit purchases (at least $10, with threshold handling described by OpenRouter). Failed requests can count. Key's `limit_remaining` means **potential paid spending headroom**, not a free model balance.

Groq's catalog may return response rate headers; when present, daily request and minute token counters are parsed separately. If absent, the result is **UNKNOWN**; consult your organisation's Groq limits page. NVIDIA's model catalogue does not prove account-free allowance. Nous Cloud's current account/endpoint terms must be checked via official provider/dashboard; `NOUS_API_KEY` presence is detected but no unverified endpoint is called. Local Ollama reports an unmetered local model, not free electricity or guaranteed speed. Different AI applications and agents may have separate quota and terms: a shared discovery script does not magically discover or bypass their subscription counters.

**No keys are printed or cached**, no request is made to a remote chat endpoint, and status headers are not dumped. On 401/402/429, agents must stop that provider rather than send a paid fallback. `usage.mjs` is an account-information helper, not a background scheduler, monitoring service or autonomous router.

## Meters, usage-window planner and overnight schedule (v2)

Every allowance is one flat **meter** (`contract.mjs`): `provider_kind` (API | APP | LOCAL | MANUAL), `model_id`, `task`, `verified_free`, `price`, `used`, `remaining`, `limit`, `window`, `reset_at`, `checked_at`, `confidence`, `privacy`, `eligibility`, `unknown_reason`, `evidence_url`. Unknown stays unknown: stale counters, counters that disagree, missing accounts, unparseable prices and unsafe model IDs are never `eligible`.

**Subscription apps (Claude, Fable, Antigravity, ChatGPT) are APP meters from an editable manual snapshot**, labelled `confidence: manual`. Scout never logs in, scrapes or reads cookies. Copy `tests/manual-snapshot.example.json`, edit the numbers from the app yourself, update `checked_at`.

```bash
node planner.mjs --snapshot my-snapshot.json                  # one-line summary
node planner.mjs --snapshot my-snapshot.json --json --hours=8 # JSON plan
node planner.mjs --snapshot my-snapshot.json --tz=Europe/London --now=2026-11-15T18:30:00Z --json
```

The plan answers three things: what can run now (with a bounded budget that keeps a 20% reserve), when the next window opens, and an **overnight schedule** split at every reset (resets step by their window; day/week windows keep the local wall-clock time across DST). Lanes are free API, prepaid subscription and local only; there is no paid lane. A reset time in the past is treated as an assumed refill (low confidence); a missing reset is never invented (`needs_input`).

Safety state (`guard.mjs`, ideas ported from the owner's mazrouter): a zero-spend guard before any request, a cooldown after 401/402/429 (stop, no retry, no key rotation, no fallback), rate-limit-only header memory, and a **metadata-only** JSONL usage ledger (no prompts, outputs or keys). Ranking uses measured success/latency from that ledger first (needs 2+ runs), then the labelled metadata heuristic. State lives next to the model cache (`PIBLO_SCOUT_CACHE` to move it).

## Installation as an agent skill
Copy or symlink **this single folder** into the agent's supported skills directory, or have the agent read `SKILL.md` directly. Read the host's native skill instructions first. The presence of files does not prove a CLI has loaded them. No global agent configuration is changed automatically. Example install:
```bash
# example only — check the active agent's supported skill directory first
cp -r skills/free-model-scout ~/.codex/skills/piblo-free-model-scout
```

## Test
```bash
node --test skills/free-model-scout/tests/*.test.mjs
```
Fixture-driven tests make no external API calls. The GitHub Actions workflow runs offline tests. No service credentials are needed for CI.

## Limits and privacy
- Free availability and terms vary, API listing != quota and $0 != unlimited. Read <https://openrouter.ai/collections/free-models>, <https://console.groq.com/docs/rate-limits> and <https://docs.api.nvidia.com/nim/docs/product>.
- The per-task ranking is based on public metadata and name hints; accuracy, tool correctness and coding reliability require controlled task benchmarks. One arithmetic probe cannot prove general quality.
- This tool does not authenticate SSH, launch subagents, install their model providers, use secrets files or write to Piblo's application state.
- Optional remote requests can transmit synthetic text only. For sensitive code/data, use local inference or a personally approved data-processing provider.

## Why this exists
Inspired by, but does not copy code from, <https://github.com/mnfst/awesome-free-llm-apis> (CC0) and <https://github.com/YeomanYe/free-llm-router>. Their directories teach provider selection and routing; Piblo adds explicit fail-closed free model eligibility, opt-in probes and a small agent-friendly JSON interface. No claim is made that this is independently better than every existing repo; evaluate against the acceptance plan before saying so.

MIT licence applies only to `skills/free-model-scout/`. The rest of Piblo remains under its existing licence.
