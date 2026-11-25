# Feature: Device capabilities over the universal registry

- **Date:** 2026-11-22
- **Objective:** Expose Piblo handheld device connection & pairing through the universal capability contract (GET /capabilities, POST /capabilities/{id}/execute), so PibloCraft and the host can discover and act on device state via the same contract as PC controls, screen perception, and reminders.
- **Why it matters:** Device pairing and connection status are part of the same execution/data model as every other Piblo capability — events publish on the bus, the router can route to them, and approval is explicit (device_control permission).
- **Reused Components:** `host/mazhost/device.py` (DeviceMonitor — serial follow with reconnection), `host/mazhost/pairing.py` (DevicePairingStore — device request/poll/approve/deny state machine), `host/mazhost/universal_registry.py`.
- **Code Locations:** `host/mazhost/registry_routes.py` (device.* handlers + registration), `host/mazhost/app.py` (shared DevicePairingStore passed to both /pair and registry), `host/mazhost/pairing.py` (build_pairing_app accepts optional external device_store), `host/tests/test_device_capabilities.py`.
- **Implemented Functionality:** Capabilities registered when deps provided: `device.status` (monitor status: connection_state/port/reconnects/last_seen/last_error), `device.monitor.start` / `device.monitor.stop` (open/close handheld serial port — hardware only touched on explicit start), `device.pairing.pending` / `device.pairing.approve` / `device.pairing.deny` (shared store with /pair; approve requires request_id, optional 6-digit code; no hardware touched). Each execution publishes `capability.execution.*` events. Unknown/invalid request → success:false with reason.
- **No hardware accessed:** Nothing opens a serial port unless `device.monitor.start` is explicitly called; pairing approve/deny only mutates in-memory request state.
- **Remaining (hardware-dependent, NOT coded speculatively):** flash firmware to a real Cardputer, pair a physical device over USB/ESP-NOW, verify reconnection across USB resets, verify low-power emergency reading state. Recorded in TEST-BATCH.md.
- **Try it:** `registry.execute("device.status", {}, {})`; `registry.execute("device.pairing.pending", {}, {})`.
- **Commit/PR:** `feat/core-event-bus-v2`, PR #215.
- **Status:** Built (host-side handlers + 5 tests passing; hardware verification deferred to TEST-BATCH).
