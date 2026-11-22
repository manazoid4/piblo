# Feature: Build Ledger & Core Event Architecture

- **Date:** 2026-11-22
- **Objective:** Establish the authoritative build ledger (`docs/build-ledger/`) across the Piblo repository and define the unified event bus and capability contracts.
- **Why it matters:** Provides an unambiguous engineering record and standardized data structures connecting Piblo Core, PibloCraft, Flow, and physical hardware.
- **Reused Components:** Unified memory database conventions, existing Piblo Core python modules.
- **Code Locations:** `host/mazhost/universal_registry.py`, `host/tests/test_registry.py`, `docs/build-ledger/`.
- **Implemented Functionality:** Complete build ledger directory structure plus `UniversalCapabilityRegistry` + lightweight `EventBus` with async publish/subscribe, bounded event history, capability contract (schema, permissions, handler, version, result/evidence). Core REST exposure: `GET /capabilities`, `POST /capabilities/{cap_id}/execute` (`host/mazhost/registry_routes.py`) wired into `host/mazhost/app.py`. Tier 0 deterministic handlers registered: `pc.*` (desktop, play_pause, mute, volume_down/up, previous/next_track, lock via PCController), `reminder.create` (via buddy.post_alert), `system.status` (via MazCore.status). Unit tests (2/2) + route-wiring check with mocked PCController/buddy passed (no real keypresses).
- **Remaining:** Tier 1 tiny command model routing; PibloCraft capability discovery UI; OCR/screen perception; voice/Flow integration.
- **Testing Requirements:** `pytest host/tests/test_registry.py` → 2 passed; route-wiring check passed; hardware execution deferred to `TEST-BATCH.md`.
- **Status:** Built (commit on `feat/core-event-bus-v2`, PR #215 open against `main`).
