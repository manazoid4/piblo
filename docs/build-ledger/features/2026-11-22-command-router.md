# Feature: Command router (Tier 0 deterministic + Tier 1 small-model)

- **Date:** 2026-11-22
- **Objective:** Route a free-text request to a Piblo capability id + structured arguments, so "Piblo, open Chrome" / "volume up" / "remind me in 10 minutes to ..." resolve to a registered capability without guessing.
- **Why it matters:** Gives Piblo a single command interpretation layer. Tier 0 handles frequent direct commands deterministically (no model, zero latency, no privacy cost); Tier 1 uses the small local model to select a registered capability id and extract args for less-familiar phrasing. The router never performs actions — the registered capability implementation does.
- **Reused Components:** `host/mazhost/commands.py` (parse_command), `host/mazhost/universal_registry.py` (global_registry, capability list), `host/mazhost/llm.py` patterns (ollama `/v1/chat/completions`), `host/mazhost/config.py` (ollama_url, ollama_model).
- **Code Locations:** `host/mazhost/command_router.py`, `host/mazhost/app.py` (`POST /command/route`), `host/tests/test_command_router.py`.
- **Implemented Functionality:** `CommandRouter.route(text)` → RouteResult{tier, capability, args, confidence, reasoning}. Tier 0: deterministic pc.action / reminder.create. Tier 1: qwen3.5:4b via ollama `/v1/chat/completions` prompted to output JSON selecting ONE registered capability id; unclear/low-confidence → tier="unclear" for clarification/escalation. Gracefully degrades when ollama is unavailable or returns non-JSON.
- **Remaining:** Wire /command/route into the chat-turn flow (so natural-language turns route through Tier 0→1 before the LLM); PibloCraft discovery of routed commands; confidence-aware escalation to Tier 2/3.
- **Model decision (evidence-grounded):** Only `qwen3.5:4b` is installed in local ollama; config uses `local_engine=ollama`, `default_route=local`. Reused it rather than pulling a new tiny model (reuse-first, no network/pull). Tier 1 is model-agnostic — swapping to a smaller model later needs only a config change.
- **Try it:** `registry.list_all()` then `command_router.route("volume up")` → tier-0 pc.volume_up; route an unclear phrase → tier-1 or unclear.
- **Commit/PR:** `feat/core-event-bus-v2`, PR #215.
- **Status:** Built (router + endpoint + 6 unit tests passing with mocked httpx client; no network/ollama required for tests).
