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

Serve the app image over HTTP on the same network as the board, then:

```sh
python scripts/piblo_tool.py ota --port COM4 --url http://<host>:8799/firmware.bin
```

The board pulls the image, verifies it, writes the spare slot and reboots. If the
new image fails to boot, the bootloader rolls back to the previous slot.

There is also a web portal: join the `Piblo-Setup` AP (or the board's LAN IP),
open `http://<ip>/`, unlock with the OTA token, then POST a file to `/do-update`.

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
python scripts/piblo_tool.py setup --port COM4 --ssid "MyAP" --password "secret"
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
