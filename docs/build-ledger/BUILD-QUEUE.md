# Piblo Build Queue

1. **Shared Event & Capability Architecture (Priority 1):** Done — UniversalCapabilityRegistry + lightweight EventBus under `host/mazhost/universal_registry.py`, exposed via Core REST (`GET /capabilities`, `POST /capabilities/{id}/execute`) in `host/mazhost/registry_routes.py` and wired into `host/mazhost/app.py`. Tier 0 deterministic handlers registered: `pc.*` (desktop, play_pause, mute, volume_down/up, previous/next_track, lock via PCController), `reminder.create` (via buddy.post_alert), `system.status` (via MazCore.status). 2 unit tests passing + route-wiring check passed. Committed `feat/core-event-bus-v2`, PR #215 open against `main`.
2. **Tiny Command Model Routing (Priority 4):** Pending.
3. **Screen Perception & OCR Integration (Priority 3):** Pending.
4. **Enhanced Voice & Flow Integration (Priority 5):** Pending.
5. **Physical Device Handlers & Power Awareness (Priority 6):** Pending.
