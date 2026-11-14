#!/usr/bin/env python3
"""piblo-flash / piblo-ota - USB flash and wireless OTA for the Piblo bootstrap board.

Usage:
  piblo-flash --port COM4 --firmware factory.bin     USB flash (first install / recovery)
  piblo-ota   --port COM4 --url http://host/fw.bin    pull-and-flash over the board's own Wi-Fi
  piblo-setup --port COM4                            send Wi-Fi credentials over USB
  piblo-cmd   --port COM4 --cmd "PIBLO STATUS"       raw control command

Wi-Fi credentials are passed to the device over USB and stored in NVS. They are
never written to the image, and never printed. COM3 is refused because Piblo
Core uses it.
"""
import argparse
import json
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is required: python -m pip install pyserial")

RESERVED = {"COM3"}
BAUD = 115200


def safe(port):
    return port.strip().upper() not in RESERVED


def open_board(port):
    s = serial.Serial()
    s.port = port
    s.baudrate = BAUD
    s.timeout = 2
    s.write_timeout = 3
    # USB-Serial-JTAG: asserting DTR/RTS resets the chip. Keep both deasserted.
    s.dtr = False
    s.rts = False
    s.open()
    return s


def drain(s, seconds=1.0):
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        out += s.read(8192)
    return out.decode("utf-8", "replace")


def cmd(port, line, wait=1.5):
    with open_board(port) as s:
        time.sleep(1.8)          # let the app finish booting
        s.reset_input_buffer()
        s.write(line.encode() + b"\n")
        time.sleep(wait)
        return drain(s, 0.5)


def list_ports_safe():
    return [p for p in list_ports.comports() if safe(p.device)]


def pick_port(requested):
    if requested:
        return requested
    ports = list_ports_safe()
    if not ports:
        sys.exit("No board port found (COM3 is reserved for Piblo Core).")
    for i, p in enumerate(ports, 1):
        print(f"{i}: {p.device} - {p.description}")
    try:
        return ports[int(input("Choose the board port number: ")) - 1].device
    except (ValueError, IndexError):
        sys.exit("Invalid port selection.")


def main():
    ap = argparse.ArgumentParser(description="Piblo bootstrap board tooling")
    ap.add_argument("--port", help="serial port, e.g. COM4")
    sub = ap.add_subparsers(dest="action", required=True)

    f = sub.add_parser("flash", help="USB flash a factory image")
    f.add_argument("--firmware", required=True)

    o = sub.add_parser("ota", help="fetch a firmware URL and flash it on-device")
    o.add_argument("--url", required=True)

    st = sub.add_parser("setup", help="send Wi-Fi credentials over USB")
    st.add_argument("--ssid", required=True)
    st.add_argument("--password", default="")

    c = sub.add_parser("cmd", help="raw PIBLO control command")
    c.add_argument("--cmd", required=True)

    a = ap.parse_args()
    port = pick_port(a.port)
    if not safe(port):
        sys.exit(f"{port} is reserved for Piblo Core; refusing.")

    if a.action == "flash":
        import esptool  # provided by PlatformIO's tool-esptoolpy
        args = ["--chip", "esp32s3", "--port", port, "--before", "default_reset",
                "--after", "hard_reset", "--no-stub", "write-flash", "-z",
                "--flash-mode", "keep", "--flash-freq", "keep",
                "--flash-size", "detect", "0x0", a.firmware]
        print("Flashing", a.firmware, "to", port)
        raise SystemExit(esptool.main(args))

    if a.action == "ota":
        print(cmd(port, f"PIBLO OTA {a.url}", wait=8.0))

    if a.action == "setup":
        line = "PIBLO WIFI " + a.ssid + (" " + a.password if a.password else "")
        print(cmd(port, line, wait=2.5))

    if a.action == "cmd":
        print(cmd(port, a.cmd))


if __name__ == "__main__":
    main()
