# Zero-token audit: Core features that call an LLM (2026-11-26)

Scope: `host/mazhost` at origin/main 4e7de67. All LLM traffic goes through `Models.chat` (llm.py:626), the
Tier-1 command router (command_router.py:92), `LiveBrain` (live_brain.py:440) or the vision call (look.py:83).
Measured numbers come from `tools/piblo_benchmark.py` (router rows in `tools/bench/*.json`).

## Call sites, and whether a deterministic route could replace them

| # | Feature | LLM call (file:line) | Deterministic alternative | Verdict |
|---|---|---|---|---|
| 1 | Live voice, every non-command turn | live_brain.py:402 `stream` -> :440; caller live_voice.py:381 | Pre-route today is only mode/model/remember (live_brain.py:300-325) and `command_handler` (live_voice.py:345). `/ask` already short-circuits work-state, `parse_compound`, `parse_command`, `feature_commands`, promises (app.py:~520-570) before the LLM; live voice does not reuse that chain | Yes, reuse the /ask chain in `command_handler` |
| 2 | Command router Tier 1 | command_router.py:92-101 (`/command/route` app.py:1462; voice activity app.py:1119/1164) | Tier 0 (`parse_command`, commands.py:24) covers only pc actions + reminders. Measured: "what's next" and "open settings" fall through and cost 1 LLM call each; "volume up", "lock the pc", reminders cost 0 (0.003-0.008 ms) | Yes: add regex/slot rules from the capability registry (registry_routes.py) for device open/volume/status |
| 3 | `/ask` general chat | app.py:572 (`model_router.chat`) | Already deterministic first (app.py:~520-570). Remainder is genuinely open-ended | Partly; add an exact-question cache only |
| 4 | Reminder phrase parse | promises.py:374 `_llm_parse` (called from :501 before `_free_parse`) | `_free_parse` regex parser already exists and is the fallback; LLM is tried first, locally ("local" route, promises.py:406) | Yes: try `_free_parse` first, LLM only on failure |
| 5 | Share-in summary | share_in.py:75 | First-3-sentences fallback already computed (share_in.py:70-72) | Yes: make the LLM opt-in |
| 6 | Dictation cleanup | flow.py:167 (Groq) and :181 (`models.chat`) | `rule_cleanup` is the default (flow.py:160); LLM only when `flow_cleanup_llm` (config.py:29, default False) or Groq key + `flow_cloud` | Already deterministic by default |
| 7 | Extract / braindump | app.py:1183, :1204 | Prompts demand JSON from free text; no rule equivalent | No |
| 8 | Dump + library helpers | dump_routes.py:30, library_routes.py:32 | Classification/summary of arbitrary files | No (could cache by file hash) |
| 9 | Agent builder / learning rules / skill vault | agent_builder.py:166, learning.py:72, skill_vault.py:119 | Skill vault has `deterministic-fallback` (skill_vault.py:~116); others have fallbacks on error only | Background, low frequency; cache by input hash |
| 10 | Workflows plan/retro/etc | workflows.py:152 (`_json_model`; used :172, :196, :213) | Grounding via `core.py` context is already local; synthesis needs a model | No |
| 11 | Vision / screen read | look.py:83 (cloud vision) | Local OCR exists: registry_routes.py:83 `screen.ocr`/`screen.read` | Partly, for text-only questions |
| 12 | AI validation harness | ai_validation.py:39 | Test tool, not runtime | n/a |

## Top 5 by token/latency saving (estimate, no usage telemetry was available)

1. Live voice turns (#1): every spoken request hits a cloud/local LLM with first-token budget of seconds
   (live_brain.py FIRST_TOKEN_S); device/PC/work commands could answer in milliseconds with zero tokens.
2. Tier-1 router fallthrough (#2): a full chat completion (30 s timeout, command_router.py:101) just to pick a
   capability; measured 2 of 5 sample phrases hit it. Registry-driven regex removes it.
3. Reminder parse order (#4): a large few-shot prompt (long few-shot prompt, promises.py:380-398) on every phrase before the
   regex parser; swap the order.
4. Share-in summaries (#5): up to 12,000 chars of input per share (share_in.py:77) for a summary whose
   deterministic fallback already exists.
5. Vision/screen questions (#11): an image per call to a cloud model (look.py:83); OCR route handles
   "what does the screen say" class questions for free.

Not verified: real per-feature token counts (no usage log read); #3 cache hit rate; whether live_voice `command_handler`
is wired to the /ask chain in production (live_voice.py:345 only shows it is optional).
