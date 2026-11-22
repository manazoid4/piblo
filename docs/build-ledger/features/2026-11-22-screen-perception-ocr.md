# Feature: Screen Perception (capture + graceful OCR)

- **Date:** 2026-11-22
- **Objective:** Let Piblo read what's on screen (screenshot capture + OCR text extraction) and expose it through the universal capability registry so Core, PibloCraft, and the handheld can use structured screen content.
- **Why it matters:** Enables "read what's on the page," extract form fields, summarize documents/receipts, and prepare actions from observed screen state — a core everyday Piblo experience.
- **Reused Components:** PIL ImageGrab (already a dependency), `host/mazhost/universal_registry.py`, `host/mazhost/registry_routes.py`, `host/mazhost/app.py`.
- **Code Locations:** `host/mazhost/screen_perception.py`, `host/mazhost/registry_routes.py` (screen.* registration), `host/mazhost/app.py` (wiring), `host/tests/test_screen_perception.py`.
- **Implemented Functionality:** `capture_screen()` (primary monitor → PNG in scratch/screenshots, read-only), `ocr_text(image_path)` (tries tesseract CLI → rapidocr → paddleocr; if none installed, returns `{available: false, reason, text: ""}` — no crash, no fabricated text). Capabilities `screen.read` and `screen.ocr` registered and executable via `POST /capabilities/{id}/execute`, publishing `capability.execution.*` events.
- **Remaining:** OCR engine installation (tesseract or rapidocr_onnxruntime) — environment task, recorded in TEST-BATCH.md; do not block on network install.
- **Hardware required:** No (works on Windows host with a display).
- **Try it:** `registry.execute("screen.read", {}, {})` returns image path/size; `registry.execute("screen.ocr", {"image_path": <path>}, {})`.
- **Commit/PR:** `feat/core-event-bus-v2`, PR #215.
- **Status:** Built (capture) / Partial (OCR available only after engine install).
