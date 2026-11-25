# Feature: Live voice — dictate → command router → capability event

- **Date:** 2026-11-22
- **Objective:** Connect Piblo Flow speech-to-text output to the universal capability registry so a spoken request is classified to a capability (Tier 0 deterministic, or Tier 1 small-model qwen3.5:4b) and surfaced as a `voice.command.routed` event for PibloCraft live activity.
- **Why it matters:** Completes "speak into Piblo → Core interprets → capability selected → event published → observable in PibloCraft" without changing Flow's existing paste/log behaviour (additive, reversible).
- **Reused Components:** `host/mazhost/flow.py` (dictate, parse_prefix), `host/mazhost/command_router.py`, `host/mazhost/universal_registry.py` (global registry + event bus), `host/mazhost/config.py` (ollama_url/ollama_model).
- **Code Locations:** `host/mazhost/app.py` (dictate endpoint + command_router wiring), `host/mazhost/command_router.py` (lazy registry capability list).
- **Implemented Functionality:** In the `/dictate` endpoint, after STT cleanup, `result["text"]` is routed via `command_router.route()` (run in threadpool so tier-1 model calls don't block the event loop). A `command_route` dict (tier/capability/args/confidence) is added to `result`, and a `voice.command.routed` event is published on the registry event bus. Existing paste-to-PC and logging behaviour is unchanged; nothing is executed automatically (no surprising pc.* side effects from voice).
- **Remaining:** Auto-execution policy for high-confidence voice intents (Maz to confirm); PibloCraft live-activity UI consuming `voice.command.routed` events; Tier 2/3 escalation (more capable model/cloud, permission-gated).
- **Try it:** POST /dictate with audio → result.command_route shows tier/capability; event bus history contains voice.command.routed.
- **Commit/PR:** `feat/core-event-bus-v2`, PR #215.
- **Status:** Built (additive wiring, 12 unit tests passing, app compiles).
