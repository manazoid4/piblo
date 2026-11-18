# Reference repos: check here before writing firmware from scratch

Before writing a low-level ESP32 driver, audio pipeline, BLE/Wi-Fi pairing flow or UI state machine,
look for a working version in these repos and adapt it. Clone them all with:

```bash
bash scripts/clone_refs.sh            # -> ../piblo-refs (outside this repo, never committed)
```

## Licence rule (Piblo is PolyForm Noncommercial)

- **MIT / BSD / Apache-2.0 / Espressif MIT**: code may be copied or adapted. Keep the original
  copyright line in the file header and add the project to `NOTICE` in the same PR.
- **GPL / AGPL** (clawpal, esp_ai, ElegantOTA): **read only**. Learn the idea, write our own code.
  Never paste from them: it would force Piblo under the GPL.
- Espressif MIT (esp-sr) only allows use on Espressif chips. All Piblo boards are ESP32-S3, so it's fine.

## Map: reference → where it helps Piblo

| Repo | Licence | Use for | Piblo code it informs |
|---|---|---|---|
| MoveCall/claude-desktop-buddy-esp32 | MIT | BLE (Nordic UART) approval protocol, permission decision messages, desk-pet states | `src/core/approvals.*`, `src/ui/buddy_face.*`, `docs/ASK-ON-PIBLO.md` |
| 78/xiaozhi-esp32 | MIT | Audio service (Opus, WebSocket/UDP streaming), wake word, LVGL expressions; has `boards/m5stack/cardputer-adv` and Waveshare S3 board configs | `src/audio/live.*`, `src/ui/lvgl_ui.*`, board pin maps |
| Kaayoos/clawpal | GPL | Evolving character + PC companion sync protocol | **ideas only** for `src/ui/character.*` |
| MichalZaniewicz/esphome-waveshare-esp32-s3-audio-va | MIT | AEC + barge-in tuning on a Waveshare S3 audio board | barge-in for Buddy Talk (`src/audio/live.*`) |
| n-IA-hane/esphome-audio-stack | MIT | Full-duplex I2S/TDM + esp-sr AFE wrapper (`esp_afe`, `esp_aec`), AFE type/mode table | full-duplex audio, AEC mode choice |
| shravanmali/aura-esp32-voice-assistant | MIT | Dual-core task pinning, echo suppression, sub-second latency server | `src/audio/*` task layout, `host/` voice path |
| iamankushpandit/esp_ai | GPL | Offline wake/STT/tiny LLM, dirty-row redraws | **ideas only** for redraw strategy |
| espressif/esp-sr | Espressif MIT | WakeNet, MultiNet, AFE (noise suppression, AEC, VAD) | wake word (`docs/WAKEWORD-SETUP.md`), VAD |
| raullenchai/esp32-thinks | MIT | LAN OpenAI-compatible voice client, nightly memory distillation, latency/soak docs | `host/` brain + ledger/memory, `docs/perf/` |
| chrisdunnname/esphome-s3-box-3-lvgl | MIT | Calm LVGL UI: sleep states, screen savers, auto-clearing popups | `src/core/alerts.*`, ambient/charge screens |
| lvgl/lv_port_esp32 | (no licence file; LVGL itself is MIT) | DMA double-buffered display flush | `src/ui/lvgl_ui.*` flush path |
| mathertel/OneButton | BSD | Click / double-click / long-press on single buttons (Waveshare BOOT key, Dock keys) | board button handling (not the Cardputer matrix keyboard) |
| tzapu/WiFiManager | MIT | Captive portal + AP fallback onboarding | `src/net/portal*`, first-run setup |
| h2zero/NimBLE-Arduino | Apache-2.0 | Low-RAM BLE that coexists with Wi-Fi | future BLE pairing / approvals channel |
| ayushsharma82/ElegantOTA | AGPL | Browser OTA UX | **ideas only**: Piblo already has Core-staged OTA with a rollback guard (`host/mazhost/fw.py`) |

## Checked and ruled out

- Waveshare ESP32-S3-LCD-1.47B uses an **ST7789** controller (Waveshare wiki), matching our board file.
  The JD9853 driver in xiaozhi belongs to Waveshare's separate *touch* 1.47in module, so it does not
  explain the Waveshare LCD fault.

## Rule of thumb

1. Search `../piblo-refs` (`grep -rn <thing> ../piblo-refs`) before writing a driver.
2. Copy only from permissive licences, with attribution.
3. Any firmware change still needs a physical test, and flashing needs the owner's OK each time.
