#!/usr/bin/env python3
"""Update Piblo firmware over USB with the in-app MAZOTA command (src/net/control.cpp).

Writes Core's staged build to the spare OTA slot and reboots into it, same as pressing U,
but needs no Wi-Fi or Home prompt. Checks the staged sha256 and refuses a downgrade.

    python scripts/piblo_usb_update.py            # COM port auto-detected, Core's fw/latest.bin
    python scripts/piblo_usb_update.py --port COM3 --bin path/to/firmware.bin --sha256 <hex>
"""
import argparse, hashlib, json, os, pathlib, socket, sys, time

import serial
from serial.tools import list_ports


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def vtuple(v):
    return tuple(int(p) for p in v.split("."))


def device_version(host="192.168.1.47"):
    try:
        with socket.create_connection((host, 8022), timeout=3) as s:
            return s.recv(64).decode(errors="replace").split()[1]  # "Piblo 1.5.1 - MAZAUTH first"
    except (OSError, IndexError):
        return None


def main():
    core = pathlib.Path(os.environ.get("NOD_CORE_DIR") or pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Piblo" / "Core")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--bin", default=str(core / "fw" / "latest.bin"))
    ap.add_argument("--sha256", help="expected sha256 (default: Core's fw/manifest.json)")
    ap.add_argument("--host", default="192.168.1.47", help="device IP for the version check")
    ap.add_argument("--force", action="store_true", help="allow same or older version")
    a = ap.parse_args()

    data = pathlib.Path(a.bin).read_bytes()
    manifest = {}
    if not a.sha256:
        manifest = json.loads((pathlib.Path(a.bin).parent / "manifest.json").read_text())
        a.sha256 = manifest["sha256"]
    if hashlib.sha256(data).hexdigest() != a.sha256.lower():
        die("sha256 mismatch: not flashing")
    running = device_version(a.host)
    target = manifest.get("version")
    print(f"device {running or 'unknown'} -> image {target or 'unknown'} ({len(data)} B, sha256 ok)")
    if not a.force and running and target and vtuple(target) <= vtuple(running):
        die("image is not newer than the device (use --force to override)")

    port = a.port or next((p.device for p in list_ports.comports() if p.vid == 0x303A), None)
    if not port:
        die("no Espressif USB port found; plug Piblo in with a data cable")
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 2
    s.dtr = s.rts = False  # don't trigger the USB reset-into-bootloader
    s.open()
    s.dtr = True  # DTR alone (RTS low) does not reset; it tells the device a host is listening
    time.sleep(0.5)
    s.reset_input_buffer()
    s.write(f"MAZOTA\t{len(data)}\n".encode())

    def expect(word, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            line = s.readline().decode(errors="replace").strip()
            if line.startswith("MAZOTA ERR"):
                die(line)
            if line.startswith(word):
                return line
        die(f"timed out waiting for {word}")

    expect("READY")
    for i in range(0, len(data), 4096):
        for j in range(i, min(i + 4096, len(data)), 64):  # paced: a 4 KB burst stalls the device's USB RX
            s.write(data[j:j + 64])
            s.flush()
            time.sleep(0.003)
        expect("ACK")
        print(f"\r{min(i + 4096, len(data)) * 100 // len(data)}%", end="", flush=True)
    print()
    print(expect("MAZOTA OK", 30))
    s.close()
    if target:
        for _ in range(30):
            time.sleep(2)
            v = device_version(a.host)
            if v:
                print(f"device now reports {v}")
                sys.exit(0 if v == target else 2)
        print("device rebooted; version not confirmed over Wi-Fi yet")


if __name__ == "__main__":
    main()
