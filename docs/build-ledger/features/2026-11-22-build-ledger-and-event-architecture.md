# Feature: Build Ledger & Core Event Architecture

- **Date:** 2026-11-22
- **Objective:** Establish the authoritative build ledger (`docs/build-ledger/`) across the Piblo repository and define the unified event bus and capability contracts.
- **Why it matters:** Provides an unambiguous engineering record and standardized data structures connecting Piblo Core, PibloCraft, Flow, and physical hardware.
- **Reused Components:** Unified memory database conventions, existing Piblo Core python modules.
- **Code Locations:** `docs/build-ledger/`, `src/core/` (planned event bus).
- **Implemented Functionality:** Complete build ledger directory structure and baseline tracking documents (`INDEX.md`, `CURRENT-STATE.md`, `BUILD-QUEUE.md`, `INTEGRATION-MAP.md`, `DECISIONS.md`, `DEPENDENCIES.md`, `BLOCKERS.md`, `TEST-BATCH.md`).
- **Remaining:** Implementation of the asynchronous event bus and capability registry in `src/core/`.
- **Testing Requirements:** Verified file writing and structure validity; hardware execution deferred to `TEST-BATCH.md`.
- **Status:** Built (Ledger structure + Universal Capability Registry & Event Bus implemented; unit tests passing).
