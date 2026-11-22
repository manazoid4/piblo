# Piblo Current State (As of 22 Nov 2026)

## Working Functionality
- **Piblo site & pages (`/features/`, `/piblocraft/`):** Clean Kickstarter-style bento grid, swipeable cards, cream concept design default with smoke switch. (PR #195).
- **PibloCraft 0.7:** Mission control environment with active agent support, provider list, cswap status, and fixed `pc board` UI. (manazoid4/piblocraft PR #48).
- **Piblo Core event bus & capability registry:** `UniversalCapabilityRegistry` + lightweight `EventBus` under `host/mazhost/universal_registry.py`; exposed via Core REST (`GET /capabilities`, `POST /capabilities/{id}/execute`) in `host/mazhost/registry_routes.py`, wired into `host/mazhost/app.py`. Tier 0 handlers: `pc.*` (PCController), `reminder.create` (buddy), `system.status` (MazCore). Screen perception: `screen.read` + `screen.ocr` via `host/mazhost/screen_perception.py` (PIL ImageGrab capture; OCR degrades gracefully when no engine installed). Command router: `host/mazhost/command_router.py` + `POST /command/route` (Tier 0 deterministic + Tier 1 qwen3.5:4b via ollama selecting a registered capability). Unit tests + route-wiring + screen checks passing. PR #215.
- **Live voice:** `/dictate` (Piblo Flow STT) routes cleaned speech through `command_router` and publishes `voice.command.routed` events (tier/capability/args/confidence) for PibloCraft live activity. Existing paste-to-PC and logging behaviour unchanged; nothing executed automatically from voice. PR #215.
- **Device connection & pairing:** `device.*` capabilities over the universal registry — `device.status`, `device.monitor.start/stop` (DeviceMonitor serial follow), `device.pairing.pending/approve/deny` (shared DevicePairingStore with `/pair`). Nothing opens a serial port unless `device.monitor.start` is explicitly called. PR #215.
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
