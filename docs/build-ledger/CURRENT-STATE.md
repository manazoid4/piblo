# Piblo Current State (As of 22 Nov 2026)

## Working Functionality
- **Piblo site & pages (`/features/`, `/piblocraft/`):** Clean Kickstarter-style bento grid, swipeable cards, cream concept design default with smoke switch. (PR #195).
- **PibloCraft 0.7:** Mission control environment with active agent support, provider list, cswap status, and fixed `pc board` UI. (manazoid4/piblocraft PR #48).
- **Piblo Core event bus & capability registry:** `UniversalCapabilityRegistry` + lightweight `EventBus` under `host/mazhost/universal_registry.py`; exposed via Core REST (`GET /capabilities`, `POST /capabilities/{id}/execute`) in `host/mazhost/registry_routes.py`, wired into `host/mazhost/app.py`. Tier 0 handlers: `pc.*` (PCController), `reminder.create` (buddy), `system.status` (MazCore). Screen perception: `screen.read` + `screen.ocr` via `host/mazhost/screen_perception.py` (PIL ImageGrab capture; OCR degrades gracefully when no engine installed). Unit tests + route-wiring + screen checks passing. PR #215.
- **Firmware & Hardware baseline:** ESP32-S3 retro portrait retro-inspired design, Cardputer ADV support, Waveshare/ESP development baselines.

## Partial Implementation
- **Piblo Core & Event Architecture:** Initial local orchestration structures; unified event bus and shared capability contracts in design phase.
- **Piblo Flow (STT):** Local speech-to-text pipeline established; wake-word and push-to-talk integration in progress.

## Proposed / In Progress
- Universal tool registry extending existing tool mechanisms.
- Tiny command model routing (Tier 0-3 hybrid intelligence).
- Screen perception / OCR fallback integration.

## Blocked / Deferred
- Real device flashing and physical sensor telemetry verification (awaiting hardware testing batch).
