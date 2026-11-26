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

## Everyday intelligence (24 Nov, PR on branch ccr-eab81452-54wunq)
- [ ] Android: open Home > Find devices, Add this phone, then from the PC `POST /assist {"text":"find my phone"}` then `"ring it"`: phone rings on silent; Stop works.
- [ ] Android: Start listening with AirPods (paired to the phone) nearby; Add from "Heard nearby"; Hotter/colder fills as you walk closer.
- [ ] Android: AirPods paired to an iPhone: do Apple adverts appear as "AirPods-type (identity unverified)" with `neverForLocation`? Open Find My hand-off works.
- [ ] Windows: freeze an app (or `python -m http.server 8000` twice) and say "sort this out": diagnosis names it; "yes" asks on the Piblo device; after approval the app ends and "checked again" is reported.
- [ ] Windows: install an OCR engine; "sort this out" over a browser "DNS_PROBE" page shows the DNS finding.
- [ ] Grant `browser_history` (device approval), then "carry on where I left off" reopens the last Chrome/Edge page and file.
- [ ] Run `python scripts/benchmark_assist.py` against the real Core and record results.
