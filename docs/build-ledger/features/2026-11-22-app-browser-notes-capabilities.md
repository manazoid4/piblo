# Feature: App launch, browser search, notes capabilities

- **Date:** 2026-11-22
- **Objective:** Add the missing Priority 2 everyday actions — application launching, web browser interactions (open page / search), and personal notes — as capabilities over the universal registry, so "Piblo, open Chrome," "Search for ESP32 development boards," and "Save a note" resolve through one contract.
- **Why it matters:** Directly matches the directive's example flow (§8): open Chrome → search → read page (screen.read, done earlier) → save projects (notes.create). These are actions ordinary customers understand immediately.
- **Reused Components:** `host/mazhost/core.py:434` (`os.startfile` pattern for Windows launch), `host/mazhost/memory_notes.py` (`MemoryNotes.remember`), `host/mazhost/universal_registry.py`, `host/mazhost/registry_routes.py`, `host/mazhost/app.py`.
- **Code Locations:** `host/mazhost/registry_routes.py` (`app.launch`, `browser.open`, `browser.search`, `notes.create` handlers + registration), `host/mazhost/app.py` (wires `memory_notes` into `install_registry_routes`), `host/tests/test_app_browser_notes.py`.
- **Implemented Functionality:**
  - `app.launch(name)` — resolves via a curated app key→executable map + `shutil.which`, launches via `os.startfile` (Windows only). Requires `app_control` permission.
  - `browser.open(url)` — opens http/https URL in default browser via `webbrowser.open` (rejects non-http schemes).
  - `browser.search(query)` — opens a DuckDuckGo search URL in the default browser.
  - `notes.create(text)` — saves via `MemoryNotes.remember` (today's dated note). Requires `notes` permission.
  - All publish `capability.execution.*` events on the event bus.
- **Safety:** No auto-execution from voice/screen; app launch requires explicit `app_control` permission; `os.startfile` never runs unmocked in tests (mocked).
- **Try it:** `registry.execute("app.launch", {"name": "notepad"}, {})`; `registry.execute("browser.search", {"query": "ESP32 boards"}, {})`; `registry.execute("notes.create", {"text": "Buy milk"}, {})`.
- **Commit/PR:** new branch from origin/main, PR against main.
- **Status:** Built (7 unit tests passing, app.py compiles).
