# Piblo desk screen: first bring-up

This is a separate, experimental build for the **Waveshare ESP32-S3-LCD-1.47B**, not the Cardputer firmware. It has not been tried on a board. Do not flash without explicit approval.

## Build

```sh
pio run -e piblo-desk
```

The board uses 16 MB flash and 8 MB OPI PSRAM. The [Waveshare board documentation](https://docs.waveshare.com/ESP32-S3-LCD-1.47B) lists the ST7789 display pins (MOSI 45, clock 40, CS 42, DC 41, reset 39, backlight 46); its [example package](https://files.waveshare.com/wiki/ESP32-S3-LCD-1.47B/ESP32-S3-LCD-1.47B-Demo.zip) sets the 172 x 320 panel's column offset to 34. The RGB LED is GPIO 38; TF uses GPIO 15, 14, 16, 18, 17 and 21; the QMI8658 IMU is onboard. This first build does not use the LED, TF or IMU.

## Set up and observe (after approved flashing only)

1. Run a Core build containing `/desk/state` and its normal discovery and device-pairing routes. The Core must be reachable on the same 2.4 GHz LAN. Connect the approved-flashed board by USB. This branch has not been installed into the Windows app or the running Core.
2. From the repository folder, run `python scripts/desk_setup.py`. Choose the board from the list, enter the Wi-Fi name and hidden password, and leave the window open. The script excludes COM3, which Core uses. It requires pyserial; if Python reports `No module named serial`, install it with `python -m pip install pyserial` and rerun the setup command.
3. Compare the six-digit code on the board with the code in the Piblo app and click Allow only when they match. The existing LAN pairing protocol sends the shared Core token over plain HTTP; use a trusted network. It never auto-approves. The board stores Wi-Fi credentials and the Core token in its local flash.
4. A successful poll shows live Core state on the LCD and prints a line such as `desk: live state=thinking working=2 waiting=1` over USB, without logging the task text or token. Press Ctrl+C to stop watching. Rerun the command to request a fresh pairing after denial, expiry, or a Core address change. If the board loses Wi-Fi it retries; Core failures replace the old state with an offline message.

Boot, Wi-Fi, provisioning, pairing, display orientation and live Core state have not been tested on physical hardware. The serial setup and live display need a physical test. No screenshot endpoint, motion sensing, RGB, SD storage, OTA, or production provisioning is included.

## Offline image formats

The PlatformIO build makes `.pio/build/piblo-desk/firmware.bin`, an application image for offset `0x10000`. It is not a complete factory image. The following PowerShell command creates `.pio/build/piblo-desk/factory.bin` from the same build without contacting a device:

```powershell
python "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" --chip esp32s3 merge_bin -o .pio/build/piblo-desk/factory.bin --flash_mode keep --flash_freq keep --flash_size keep 0x0 .pio/build/piblo-desk/bootloader.bin 0x8000 .pio/build/piblo-desk/partitions.bin 0xe000 "$env:USERPROFILE\.platformio\packages\framework-arduinoespressif32\tools\partitions\boot_app0.bin" 0x10000 .pio/build/piblo-desk/firmware.bin
```

The resulting combined image starts at `0x0`: bootloader at `0x0`, partition table at `0x8000`, initial OTA metadata at `0xe000`, and app at `0x10000`. It is padded with `0xff` between components, including the NVS area `0x9000`–`0xdfff`; writing it would erase stored settings in that range. It is not a 16 MB backup and leaves the rest of flash out of the file. Board 2 and this image have not been physically tested. Flashing requires explicit approval and identification of the correct port; do not use COM3 while Core runs.

The Cardputer build's `firmware.bin` is a separate M5Launcher application image, not this factory image. The older v0.8.0 release contains a checksum-verifiable M5Launcher app image; it is not a full-flash backup or a proven copy of the installed firmware.
