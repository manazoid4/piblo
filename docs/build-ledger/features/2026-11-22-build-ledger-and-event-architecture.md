# Feature: Build Ledger & Core Event Architecture

- **Date:** 2026-11-22
- **Objective:** Establish the authoritative build ledger (`docs/build-ledger/`) across the Piblo repository and define the unified event bus and capability contracts.
- **Why it matters:** Provides an unambiguous engineering record and standardized data structures connecting Piblo Core, PibloCraft, Flow, and physical hardware.
- **Reused Components:** Unified memory database conventions, existing Piblo Core python modules.
- **Code Locations:** `host/mazhost/universal_registry.py`, `host/tests/test_registry.py`, `docs/build-ledger/`.
- **Implemented Functionality:** Complete build ledger directory structure plus `UniversalCapabilityRegistry` + lightweight `EventBus` with async publish/subscribe, bounded event history, capability contract (schema, permissions, handler, version, result/evidence). Unit tests passing (2/2).
- **Remaining:** Expose registry over Piblo Core REST/web UI and PibloCraft capability discovery.
- **Testing Requirements:** `pytest host/tests/test_registry.py` → 2 passed; hardware execution deferred to `TEST-BATCH.md`.
- **Status:** Built (commit `fcfa759`, PR #215 open against `main`).
