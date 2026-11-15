#!/usr/bin/env python3
"""piblo-flash / piblo-ota - USB flash and wireless OTA for the Piblo bootstrap board.

Usage:
  piblo-flash --port COM4 --firmware factory.bin
      USB flash. First install, or recovery when Wi-Fi is not up.

  piblo-ota --host 192.168.1.50 --firmware firmware.bin [--token TOKEN]
      Push a firmware image to the board over its own HTTP OTA endpoint. The
      token is cached in ~/.piblo/ota_token after the first use, so the
      everyday command is just:

          piblo-ota --host <ip> --firmware firmware.bin

  piblo-setup --port COM4 --ssid YouFibre-45FDB7 --password ...
      Send Wi-Fi credentials over USB. Stored in device NVS only; never
      written to the image and never printed.

  piblo-cmd --port COM4 --cmd "PIBLO STATUS"

COM3 is refused because Piblo Core uses it.
"""
import argparse
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

RESERVED = {"COM3"}
BAUD = 115200
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".piblo", "ota_token")


def safe(port):
    return port.strip().upper() not in RESERVED


# ---------------------------------------------------------------- serial ----
def open_board(port):
    import serial
    s = serial.Serial()
    s.port = port
    s.baudrate = BAUD
    s.timeout = 2
    s.write_timeout = 3
    # USB-Serial-JTAG: DTR = IO0 (BOOT), RTS = EN. Asserting either resets the
    # chip (DTR low forces ROM download mode), so keep both deasserted.
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


def pick_port(requested):
    if requested:
        return requested
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports() if safe(p.device)]
    if not ports:
        sys.exit("No board port found (COM3 is reserved for Piblo Core).")
    for i, p in enumerate(ports, 1):
        print(f"{i}: {p.device} - {p.description}")
    try:
        return ports[int(input("Choose the board port number: ")) - 1].device
    except (ValueError, IndexError):
        sys.exit("Invalid port selection.")


# ------------------------------------------------------------- firmware ----
def find_esptool():
    """Return an argv prefix that runs esptool, or None."""
    try:
        import esptool  # noqa: F401
        return [sys.executable, "-m", "esptool"]
    except ImportError:
        pass
    from shutil import which
    if which("esptool"):
        return ["esptool"]
    return None


def flash_usb(port, firmware):
    prefix = find_esptool()
    if not prefix:
        sys.exit("esptool not found. Run: pip install esptool")
    args = prefix + ["--chip", "esp32s3", "--port", port,
                     "--before", "default_reset", "--after", "hard_reset",
                     "--no-stub", "write-flash", "-z",
                     "--flash-mode", "keep", "--flash-freq", "keep",
                     "--flash-size", "detect", "0x0", firmware]
    print("Flashing", firmware, "to", port)
    raise SystemExit(subprocess.call(args))


# ----------------------------------------------------------------- OTA -----
def load_token():
    try:
        return open(TOKEN_FILE).read().strip()
    except OSError:
        return ""


def save_token(token):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    open(TOKEN_FILE, "w").write(token)


def ota(host, firmware, token):
    if not token:
        token = load_token()
    if not token:
        sys.exit("No OTA token. Run once with --token <token> (printed by the "
                 "board on the serial console, or shown in the portal).")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", token):
        sys.exit("Token looks malformed.")
    url = f"http://{host}/do-update?token={token}"

    data = open(firmware, "rb").read()
    boundary = "piblopiblo1234567890"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="firmware"; filename="fw.bin"\r\n',
        b"Content-Type: application/octet-stream\r\n\r\n",
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    print(f"POST {url}  ({len(data)} bytes)")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            print("HTTP", r.status, r.read(300).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read(300).decode("utf-8", "replace"))
        if e.code == 401:
            print("Token rejected. Re-run with a fresh --token.")
        raise SystemExit(1)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        # The board reboots as soon as the image is accepted, cutting the
        # response - that is success, not failure.
        print(f"connection ended while flashing: {e}")
    save_token(token)
    print("Image accepted. Board is rebooting into the new firmware.")
    print("Wait ~10s, then: piblo-cmd --port COM4 --cmd 'PIBLO STATUS'")


def main():
    ap = argparse.ArgumentParser(description="Piblo bootstrap board tooling")
    ap.add_argument("--port", help="serial port, e.g. COM4 (not needed for ota)")
    sub = ap.add_subparsers(dest="action", required=True)

    f = sub.add_parser("flash", help="USB flash a factory image")
    f.add_argument("--firmware", required=True)

    o = sub.add_parser("ota", help="push a firmware image over Wi-Fi")
    o.add_argument("--host", required=True, help="board IP on your LAN")
    o.add_argument("--firmware", required=True)
    o.add_argument("--token", help="OTA token (cached after first use)")

    st = sub.add_parser("setup", help="send Wi-Fi credentials over USB")
    st.add_argument("--ssid", required=True)
    st.add_argument("--password", default="")

    c = sub.add_parser("cmd", help="raw PIBLO control command")
    c.add_argument("--cmd", required=True)

    a = ap.parse_args()

    if a.action == "ota":
        ota(a.host, a.firmware, a.token or "")
        return

    port = pick_port(a.port)
    if not safe(port):
        sys.exit(f"{port} is reserved for Piblo Core; refusing.")

    if a.action == "flash":
        flash_usb(port, a.firmware)
    elif a.action == "setup":
        line = "PIBLO WIFI " + a.ssid + (" " + a.password if a.password else "")
        print(cmd(port, line, wait=2.5))
    elif a.action == "cmd":
        print(cmd(port, a.cmd))


if __name__ == "__main__":
    main()
