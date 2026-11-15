# Piblo bootstrap - Waveshare ESP32-S3-LCD-1.47B

Standalone bring-up firmware for the Waveshare ESP32-S3-LCD-1.47B (ESP32-S3R8:
16 MB flash, 8 MB OPI PSRAM, 1.47" 172x320 ST7789 LCD). This is deliberately
separate from the M5Stack Cardputer ADV firmware - it shares no sources with it.

## What it does

- LCD status screen: `PIBLO DEV`, Wi-Fi state, IP, OTA status, ESP-NOW state.
- Panel self-test on boot (solid red/green/blue fields) so a dead panel is obvious.
- Wi-Fi station mode with auto-reconnect; falls back to a `Piblo-Setup` AP
  (password `piblosetup`) when no saved network works, so Wi-Fi is always
  repairable from a phone.
- Credentials live in NVS only. Nothing is compiled into the image, and values
  are never printed.
- OTA: on-device HTTP pull (`PIBLO OTA <url>`) and a token-gated web upload.
  Two OTA slots with automatic rollback if the new image never marks itself valid.
- ESP-NOW link to the Cardputer ADV: PING/PONG, DEVICE_STATUS, DISPLAY_MESSAGE,
  BUTTON_EVENT, with ACKs, retries, heartbeat and channel tracking.

## Board facts (verified on hardware)

| Item | Value |
|---|---|
| Chip | ESP32-S3 QFN56, revision v0.2 |
| Flash | 16 MB quad (eFuse), 80 MHz |
| PSRAM | 8 MB OPI (AP Memory), 80 MHz |
| Clock | 40 MHz XTAL |
| USB | USB-Serial/JTAG (VID 303A / PID 1001) |
| LCD | ST7789, 172x320, column offset 34 |
| LCD pins | MOSI 45, SCLK 40, CS 42, DC 41, RST 39, BL 46 |
| RGB LED | GPIO 38 |
| Factory firmware | ESP-IDF v5.5.1, project name `ESP32-S3-LCD-1.47B` |

A 1 MB backup of the factory image (bootloader + partition table + app) was read
over USB before flashing and is kept outside this repository - do not commit it.

## Build

```sh
pio run -e piblo-bootstrap
```

The build produces `firmware.bin` (app image for offset 0x10000) and, after
merging, `factory.bin` (full image starting at 0x0) for first-time USB flashing.

## USB flash (first install, or recovery)

```sh
python scripts/piblo_tool.py flash --port COM4 \
    --firmware .pio/build/piblo-bootstrap/factory.bin
```

`piblo_tool.py` refuses COM3, which Piblo Core uses.

## Wireless update

With the board on your LAN:

```sh
python scripts/piblo_tool.py ota --host <board-ip> \
    --firmware .pio/build/piblo-bootstrap/firmware.bin
```

The token is cached in `~/.piblo/ota_token` after the first run, so the everyday
command is just `piblo-ota --host <ip> --firmware firmware.bin`.

The board writes the spare OTA slot, verifies it and reboots. If the new image
never marks itself valid, the bootloader rolls back to the previous slot.

There is also a web portal: join the `Piblo-Setup` AP (or open the board's LAN
IP), enter the Wi-Fi form, unlock with the OTA token, then use
`GET /update` to upload a file.

## USB serial control lines (important)

This board has one USB port on the ESP32-S3 native USB pins, wired to the
**USB-Serial/JTAG** peripheral. On that peripheral:

- `DTR` = **IO0 (BOOT)** - asserted (True) forces ROM download mode
- `RTS` = **EN (reset)** - asserted (True) holds the chip in reset

So a serial script must leave both deasserted, or every open resets the board
(and DTR low parks it in download mode). `piblo_tool.py` does this.

Do **not** call `USB.begin()` / `HardwareSerial(0)` on this board: that starts
TinyUSB on the same pins as a second USB device, and Windows fails to install
the composite driver (Device Manager problem code 28, port disappears).

If the chip is parked in ROM download mode and a software reset will not release
it, power-cycle the board (unplug USB-C, wait 5 s, replug).

## Serial control (USB)

The board accepts single-line commands on the USB serial port. Credentials are
written straight to NVS and never echoed.

```
PIBLO WIFI <ssid> <password>   save Wi-Fi and reboot
PIBLO SCAN                     list visible 2.4 GHz networks
PIBLO TOKEN                    print the OTA token
PIBLO OTA <url>                pull and flash firmware over HTTP
PIBLO NET                      Wi-Fi mode/state/channel/IP
PIBLO STATUS                   one-line status
PIBLO FORGET                   wipe settings and reboot
```

```sh
python scripts/piblo_tool.py setup --port COM4 --ssid "MyAP" --password "..."
python scripts/piblo_tool.py cmd  --port COM4 --cmd "PIBLO STATUS"
```

## ESP-NOW pairing

Hold the BOOT button for ~1 s to broadcast a `PeerHello`. A Cardputer ADV in
pairing mode replies, and this board stores its MAC in NVS. From then on both
devices talk directly (PING/PONG heartbeat every 3 s, ACK with 3 retries,
peer declared stale after 12 s silent). ESP-NOW follows the station channel
automatically.

## Files

```
partitions-bootstrap.csv   16 MB layout with two 5.6 MB OTA slots
src_bootstrap/main.cpp     LCD, Wi-Fi STA/AP, OTA, ESP-NOW, serial control
src_bootstrap/piblo_cfg.*  NVS settings (credentials never logged)
src_bootstrap/piblo_now.*  ESP-NOW message layer
src_bootstrap/piblo_portal.*  HTTP portal + authenticated OTA
src_bootstrap/piblo_screen.*  LCD test-pattern endpoint
scripts/piblo_tool.py      piblo-flash / piblo-ota / setup / cmd
```

## Hardware test log (14 Nov 2026)

Verified on the connected board (COM4, USB-Serial/JTAG, MAC 80:45:6b:35:d7:90):

| Check | Result |
|---|---|
| Chip probe (esptool flash-id) | ESP32-S3 QFN56 rev v0.2, Wi-Fi + BT5 LE, 40 MHz XTAL |
| Flash / PSRAM | 16 MB quad, 8 MB OPI PSRAM enabled |
| Factory backup | 1 MB read over USB before flashing (kept off-repo) |
| USB flash | factory.bin written at 0x0, hash verified |
| Boot | `piblo: bootstrap 1.1.0 starting`, PSRAM enabled |
| LCD | ST7789 drives: boot self-test fields + status screen (user confirmed) |
| Wi-Fi scan | 8 real 2.4 GHz networks, incl. `YouFibre-45FDB7` ch6 |
| Setup AP | `Piblo-Setup` up at 192.168.4.1 |
| Portal | HTTP up, OTA token generated |
| Serial control | TOKEN / STATUS / SCAN / NET answered over USB |
| ESP-NOW init | `ESP-NOW: ready` (needs STA+AP mode; AP-only init fails) |
| USB recovery | power cycle returns the board from ROM download mode |

Not verified yet, and why:

- **Joining a real network** - in progress: the board's scan sees
  `YouFibre-45FDB7` on ch6 (2.4 GHz); credentials are being supplied through the
  phone portal.
- **End-to-end OTA** - needs the board and the PC on one network.
- **ESP-NOW handshake** - the Cardputer ADV was not connected to USB during the
  session.

## Recovery

Two distinct failure modes, both recovered on hardware:

1. **COM port vanishes, Device Manager problem code 28
   (`CM_PROB_FAILED_INSTALL`).** Caused by a build that enabled a second USB CDC
   port (`USB.begin()`) alongside the USB-Serial/JTAG. The current firmware does
   not do this. Hold BOOT, tap RESET, release RESET then BOOT to enter the ROM
   download mode (which enumerates cleanly), then re-flash `factory.bin`.
2. **Chip parked in ROM download mode** (`boot:0x1 (DOWNLOAD)`, serial shows
   `waiting for download`, and esptool still syncs with `--before no_reset`).
   Software resets do not release it. Power-cycle the board: unplug USB-C, wait
   5 s, replug.
