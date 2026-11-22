# Piblo Build Queue

1. **Shared Event & Capability Architecture (Priority 1):** Done — UniversalCapabilityRegistry + lightweight EventBus exposed via Core REST (`GET /capabilities`, `POST /capabilities/{id}/execute`) in `host/mazhost/registry_routes.py`, wired into `host/mazhost/app.py`. Tier 0 handlers: `pc.*` (PCController), `reminder.create` (buddy), `system.status` (MazCore). 2 unit tests + route-wiring check passed. PR #215.
2. **Screen Perception & OCR Integration (Priority 3):** Done (capture) / Partial (OCR). `host/mazhost/screen_perception.py` captures via PIL ImageGrab; `ocr_text` tries tesseract → rapidocr → paddleocr, degrades to `{available: false}` when none installed (no crash, no fabricated text). Capabilities `screen.read` + `screen.ocr` registered. OCR engine install is a TEST-BATCH environment task (not blocking).
3. **Tiny Command Model Routing (Priority 4):** Pending — small-model intent/tool selection on top of Tier 0 handlers.
4. **Enhanced Voice & Flow Integration (Priority 5):** Pending — connect Piblo Flow STT into the Core event loop + PibloCraft live activity.
5. **Physical Device Handlers & Power Awareness (Priority 6):** Pending — ESP-NOW/USB link, low-power emergency reading.
