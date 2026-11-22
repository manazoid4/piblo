# Piblo Test Batch

## Piblo Standard Hardware
- [ ] Flash firmware to ESP32-S3 portrait retro board and test rotary encoder navigation.
- [ ] Test push-to-talk and local wake-word response.

## Windows Piblo Core & PibloCraft
- [ ] Verify `pc migrate` and active pane telemetry in PibloCraft 0.7.
- [ ] Test local event bus and capability execution (`GET /capabilities`, `POST /capabilities/{id}/execute`).

## OCR and computer control
- [ ] Install an OCR engine (tesseract or rapidocr_onnxruntime via `uv pip install`) so `screen.ocr` returns real text; verify on a sample screenshot.
- [ ] Verify `screen.read` capture on the Windows host.

## Piblo Standard hardware (physical testing batch)
- [ ] Flash firmware to ESP32-S3 portrait retro board and test rotary encoder navigation.
- [ ] Test push-to-talk and local wake-word response.
- [ ] Pair a real Cardputer over USB and ESP-NOW; verify `device.status` reports connected and reconnects across a USB reset.
- [ ] Approve a physical device pairing request via `device.pairing.approve` + the 6-digit code; verify the device receives the token.
- [ ] Verify low-power emergency reading state (locally stored books/Qur'an readable with display off/minimal power).

## Companion & OCR
- [ ] Test Android companion pairing and handoff.
- [ ] Validate OCR screen perception fallback on sample UI screenshots.
