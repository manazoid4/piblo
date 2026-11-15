# Piblo × ChatGPT — build and display handover (15 Nov 2026)

## ChatGPT bridge — implemented on this branch

This branch adds an OPTIONAL ChatGPT-facing adapter to host/mcp, without changing Cardputer firmware, Piblo Core, or the existing stdio MCP entry for Claude and Codex.

- host/mcp/src/remote.ts — authenticated Streamable HTTP MCP on localhost port 8791, with distinct 32+ character secret, 256 KiB request cap, origin guard, and healthz.
- host/mcp/src/chatgpt.ts — reuses five existing MCP tools, adds read-only piblo_overview and a tiny MCP Apps UI resource. No Core credentials or arbitrary commands in the iframe.
- host/mcp/test/remote.test.mjs — planned integration coverage for auth, MCP handshake, tool listing, and widget resource. Tests require Node and installed repo dependencies.

Start Piblo Core as usual (default localhost:8787). On Windows PowerShell, from repository root:

    cd host/mcp
    npm ci
    npm run build
    $env:PIBLO_REMOTE_TOKEN = (python -c "import secrets; print(secrets.token_urlsafe(48))")
    node dist/remote.js

MCP URL: http://127.0.0.1:8791/mcp. Use Authorization: Bearer with the separately generated PIBLO_REMOTE_TOKEN. This is a developer-only single-user bridge, not a public multi-tenant app. Do not expose Piblo Core or port 8787; use supported Secure MCP Tunnel or a trusted authenticated HTTPS tunnel for remote testing. Production requires OAuth, account/device isolation, per-user permissions and revocation. ChatGPT custom-app support depends on account/workspace access; this does not attach to any existing ChatGPT web conversation by itself.

Realtime voice stays on the direct Cardputer → Core → speech/AI provider path. MCP is for control, not speech.

## EXACT Waveshare display contract — use this, do not guess

Board: Waveshare ESP32-S3-LCD-1.47B, NON-TOUCH version. Official docs: https://docs.waveshare.com/ESP32-S3-LCD-1.47B

- Physical panel: 1.47-inch diagonal TFT, visible native resolution **172 × 320 pixels**.
- Rotation to landscape yields **320 × 172 pixels**; verify display.width() == 320, display.height() == 172 after setRotation(1). Physical portrait is 172 wide, 320 tall.
- Controller: ST7789 SPI, column offset 34 reported by PR #148. LCD pins: MOSI GPIO45, SCLK GPIO40, CS GPIO42, DC GPIO41, RESET GPIO39, BACKLIGHT GPIO46.
- MCU: ESP32-S3R8, 16MB flash, 8MB PSRAM, 2.4GHz Wi-Fi, ESP-NOW. RGB LED GPIO38. Onboard QMI8658 IMU; verify axes on actual hardware.
- There is **NO TOUCHSCREEN**. All navigation comes from Cardputer ADV keyboard/buttons.
- Do not infer millimetre dimensions or mounting positions from the screen diagonal. Use Waveshare's official 3D/dimensions drawing and physical measurements for an enclosure.

### Required LANDSCAPE pixel coordinates

Canvas x=0..319, y=0..171. Safe inset: x=8..311 and y=8..163.
Header y=8..28 (21 px tall), main content y=34..134 (101 px), footer/status y=142..163 (22 px).
Buddy face max 96×96 px in x=8..103, y=36..131. Text region x=112..311, y=42..127.
Focus: task headline clipped to fit, large readable countdown and minimal approval state.
Voice: start listening animation INSTANTLY on Cardputer Talk press (not after network or AI response); then thinking/speaking states, at most 1–2 readable lines of clipped transcript/answer.

No scrolling for core information; no overflow; test long text using actual font pixel measurement and ellipsis. UI must gracefully handle Wi-Fi offline and device pairing. Render locally without waiting for network; use dirty regions or framebuffer where suitable; do not block UI drawing on HTTP calls. Verify physical orientation and full bounds with real hardware.

### Integration state and blockers

The latest Waveshare work is open PR #148: https://github.com/manazoid4/piblo/pull/148

- piblo-bootstrap firmware has hardware-verified USB flash, ST7789 output, Wi-Fi scan and setup AP; real LAN join, OTA end-to-end and Cardputer ESP-NOW handshake remain unverified.
- piblo-desk is separate experimental UI/Core pairing implementation and is NOT confirmed flashed. Do not replace the working bootstrap firmware with it.
- Combine Desk UI and authenticated Core state into the bootstrap base so Wi-Fi, OTA, setup AP, USB recovery and ESP-NOW survive.
- Known defect: src_bootstrap/main.cpp calls sendRaw(nullptr, hello) for pairing; src_bootstrap/piblo_now.cpp rejects null peer MAC. Fix explicit broadcast registration and matching Cardputer-side ESP-NOW implementation, then verify real two-way delivery and channels.
- Current src_bootstrap/piblo_screen.cpp returns generated checkerboard pixels, NOT a capture of the Waveshare LCD. Do not claim it is a true Live View screenshot.
- Cardputer Live View exists in the Windows companion. If adding live Waveshare capture, use the real framebuffer or else use real camera photos / serial evidence honestly. Never assume the two devices share the same display dimensions.

## Next agent — finish in this order

1. Run host/mcp npm ci, npm test and remote MCP client handshake; fix compile/test errors on this branch without touching unrelated work. Verify existing stdio tools still work. ChatGPT app install is a separate test and may depend on account access.
2. On an isolated Waveshare integration branch based on PR #148, unify bootstrap + Desk without losing OTA or recovery, then prove real Wi-Fi and Core pairing.
3. Build exactly three 320×172 landscape screens: Buddy (Idle/Listening/Thinking/Speaking/Needs you), Focus (real Core task/timer), Voice (immediate talk state/short streamed reply). Keep controls on Cardputer.
4. Use actual Cardputer Live View and connected Waveshare display photos/capture. Validate coordinate bounds, long text, lost Wi-Fi, interaction and responsiveness; separate simulated tests from hardware verification.
5. Push tested PR(s), preserve dirty checkouts and other agents' branches, don't merge or change deployed firmware without any required approval. Update unified handover/Notion if accessible.

Proof demo: Talk on Cardputer → Waveshare changes to Listening without cloud wait → Core processes request → Waveshare shows response state → Focus key shows real current task and timer within 320×172 px.
