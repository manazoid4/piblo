# Save My Place recall — get / delete / export — 19/20 November 2026

Feature-build prompt: `piblo-private/prompts/feature-build/02-SAVE-MY-PLACE-RECALL.md`.
Builds on the existing library (`library.py`, FTS5 search, canonical-URL dedupe, #127) and does
not duplicate open PR #194 (Share to Piblo / Feed Vault intake + `/library/recent`).

## What changed in this PR

- `library.py`: `get_item`, `delete_item` (revocation; idempotent; hostile-id refusal),
  `export_items` (portable frontmatter + sections, re-importable via `save_items`).
- `library_routes.py`: `GET /library/item/{id}`, `DELETE /library/item/{id}`, `GET /library/export`
  (all under the existing bearer token).
- `test_library.py`: +9 tests (round-trip incl. notes/provenance, 6 hostile ids, route auth/404s).

## Evidence

| # | Command | Output | Result |
|---|---|---|---|
| 1 | `python -m pytest tests/test_library.py -q` (after change) | 43 passed, 0 failed (exit 0) | PASS |
| 2 | `python -m pytest tests -q` (host, after change, `MAZ_OLLAMA_URL=http://127.0.0.1:9`) | 1014 passed, 3 skipped, 0 failed (exit 0) | PASS |

Synthetic items only (example.com URLs, synthetic handles). No network, no scraping, no embeddings.

### Full-suite flake note (environmental, not this diff)

Two full-suite runs each failed ONE unrelated test, a different one each time:
`test_catchup.py::test_phone_alerts_survive_restart_and_silent_senders_stay_out` (run 1) and
`test_agent_builder.py::test_routes_behind_bearer_token` (run 2, level `ask_on_piblo` vs
`just_do_it`). Both files pass standalone in this worktree (38 passed, exit 0). Root cause:
`agent_builder.build_agent` uses the local Ollama brain as an ADVISORY parser ("any failure
falls back to rules"); under concurrent load (another agent batch was running device HIL tests
on this machine) its answers vary, and catchup's `due > now` filter is sensitive to clock steps.
This diff only ADDS library functions/routes (72 insertions, 0 deletions in existing code) and
cannot reach either path. Run 3 was therefore executed with `MAZ_OLLAMA_URL=http://127.0.0.1:9`
(advisory brain unreachable → deterministic rule-based fallback, the suite's designed path).

## Not proven / not in this PR

- "Propose next step with permission" follow-through (app/#194 side).
- "Saved link only" UI label for caption-less items (app side; the store already keeps them as links).
- Real Instagram export import was exercised by the existing suite only (synthetic zip fixtures).
