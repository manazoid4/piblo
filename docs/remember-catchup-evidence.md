# Lane A: remembered conversations and catch-up

## Slice 1 build brief

1. Existing files reused: memory_notes.py, app.py answer/grounded_messages, live_brain.py, existing speech response paths and isolated preview launcher.
2. Gap: ordinary Ask/PTT did not save explicit memories or retrieve daily notes.
3. Slice: 60 minutes, beginning 2026-11-13 07:00 UTC; normal Ask/PTT save and fresh-session recall.
4. Checks: focused regression tests, isolated Core restart proof on :18837, read-only diff review.
5. Live proof: synthetic appointment saved, temporary Core restarted, fresh conversation recalled Friday at noon using the configured local model.

## Slice 1 evidence

- `cd host; python -m pytest tests/test_ask_memory.py tests/test_memory_notes.py tests/test_app.py tests/test_turn_stt.py -q --disable-warnings --tb=short --basetemp=.pytest_cache/ask-final`: 39 passed (same files also run from repository root).
- Before: five feature regressions failed, including missing saves/context and invalid-session handling. Review added a failing spoken-hesitation regression; the fix preserves the raw fact.
- `python scripts/feature-preview.py --check-memory`: passed twice with real local inference, synthetic data, temporary data directories and separate Core on 127.0.0.1:18837. After restart, the model answered: "Your appointment is Friday at noon."
- Authentication rejected requests without a bearer token. The launcher stopped only the child Core it created.
- Independent read-only review: one hesitation defect fixed; second review found no remaining acceptance failures.

## Exact limits

- Ask text and ordinary uploaded/raw push-to-talk use this memory path. PTT tests inject a synthetic transcript; they do not prove microphone recognition or audible playback.
- Direct local/private live speech-to-speech bypasses LiveBrain and is not covered. Existing cloud LiveBrain daily-note tests pass; no cloud/hardware claim.
- Recall uses lexical topics, seven recent days by default (explicit relative dates can select another range), and at most 6,000 context characters. Source Markdown remains authoritative. No automatic recording, embeddings, correction/forget UI or new database.
- The preview is a backend proof with real local inference, not an Electron visual test. Installed Core, llama-swap, Kokoro and firmware were not changed or restarted.
- No release, deployment, merge or production-readiness claim. Push/PR status is recorded in the final handoff.

## Slice 2: spoken "Catch me up"

- `answer()` routes exact intents ("catch me up", "what did I miss/have I missed", optional "please"/fillers) to the existing `Catchup.catch_me_up`, deterministic (no model), read-only. Not routed when screen context is attached or the phrase has extra words.
- Tests (in `test_ask_memory.py`): real stored alert + Library save are spoken and end "Everything else can wait."; empty state says "up to date"; 53 passed across ask_memory, memory_notes, catchup, app, turn_stt.
- `python scripts/feature-preview.py`: isolated Core :18837 passed; a clearly synthetic alert returned "People who matter: Mum Synthetic: call tonight. Everything else can wait."
- Limits: synthetic alert via /buddy/alert, not a real Android notification; no real calendar adapter; LiveBrain/cloud voice and direct local speech-to-speech do not route Catch me up; no audible playback tested.
