#!/usr/bin/env python3
"""See and drive your Piblo from any agent or terminal, through Piblo Core. Zero setup on this PC.

    python scripts/piblo_remote.py screen            -> prints the path of a fresh 3x PNG of the LCD
    python scripts/piblo_remote.py keys "down*2 enter" -> presses keys, then prints a fresh screenshot path
    python scripts/piblo_remote.py open settings      -> opens an app, then prints a fresh screenshot path
    python scripts/piblo_remote.py status             -> one line: app, battery, charging, Wi-Fi
    python scripts/piblo_remote.py volume 40 | brightness 70

Key names: letters/digits, enter esc space tab backspace up down left right (repeat with *N, e.g. down*3).
Finds Core and its token like the Piblo app does: PIBLO_CORE_URL / PIBLO_TOKEN (or MAZ_TOKEN / NOD_TOKEN)
env, else <NOD_CORE_DIR or %LOCALAPPDATA%\\Piblo\\Core>\\.env, else this repo's host/.env. The token is
never printed. Screenshots go to %TEMP%\\piblo-shots (or --out). Standard library only.
"""
import json
import os
import pathlib
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def _env_file_values() -> dict:
    dirs = [os.environ.get("NOD_CORE_DIR", ""),
            str(pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Piblo" / "Core"),
            str(REPO / "host")]
    for d in dirs:
        f = pathlib.Path(d) / ".env" if d else None
        if f and f.is_file():
            vals = {}
            for line in f.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                k, sep, v = line.strip().partition("=")
                if sep and not k.startswith("#"):
                    vals[k.strip()] = v.strip().strip('"').strip("'")
            if vals.get("MAZ_TOKEN", "change-me-before-first-run") != "change-me-before-first-run":
                return vals
    return {}


ENV = _env_file_values()
TOKEN = os.environ.get("PIBLO_TOKEN") or os.environ.get("MAZ_TOKEN") or os.environ.get("NOD_TOKEN") or ENV.get("MAZ_TOKEN", "")
CORE = os.environ.get("PIBLO_CORE_URL") or f"http://127.0.0.1:{ENV.get('MAZ_PORT', '8787')}"


def call(path, method="GET", data=None):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(CORE + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        die(f"Piblo Core is not running at {CORE} ({e.reason}). Open the Piblo app, or start Core.")


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def png_from_rgb565le(raw: bytes, scale: int, w: int = 240, h: int = 135) -> bytes:
    if len(raw) != w * h * 2:
        die(f"unexpected frame size {len(raw)}")
    rows = []
    for y in range(h):
        line = bytearray()
        for x in range(w):
            v = raw[(y * w + x) * 2] | raw[(y * w + x) * 2 + 1] << 8
            line += bytes(((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31)) * scale
        rows += [b"\x00" + bytes(line)] * scale
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w * scale, h * scale, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))


def screen(out: str | None, scale: int) -> str:
    code, body = call("/core/cardputer/screen")
    if code != 200:
        die(f"screen {code}: {body.decode(errors='replace')[:200]}"
            + (" (is Piblo on and connected to this PC?)" if code == 503 else ""))
    path = pathlib.Path(out) if out else pathlib.Path(os.environ.get("TEMP", "/tmp")) / "piblo-shots" / f"piblo-{time.strftime('%Y%m%d-%H%M%S')}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_from_rgb565le(body, scale))
    return str(path)


def main(argv, out, scale, settle):
    if not TOKEN:
        die("no Piblo token found. Install the Piblo app (it sets up Core), or set PIBLO_TOKEN.")
    if not argv:
        print(__doc__); return 1
    cmd, args = argv[0], argv[1:]

    if cmd == "status":
        code, body = call("/core/cardputer/status")
        if code != 200:
            die(f"status {code}: {body.decode(errors='replace')[:200]}")
        d = json.loads(body)
        print(f"app={d.get('app')} battery={d.get('battery')}% charging={d.get('charging')} "
              f"wifi={d.get('wifi_ssid') or 'offline'} ip={d.get('ip')} core_link={d.get('host_link')} v{d.get('version')}")
        return 0

    if cmd in ("screen", "shot"):
        print(screen(args[0] if args else out, scale)); return 0

    if cmd == "keys":
        if not args:
            die("keys needs a spec, e.g. 'down*2 enter'")
        code, body = call("/core/cardputer/key", "POST", {"keys": " ".join(args)})
        if code != 200 or not json.loads(body or b"{}").get("ok", False):
            die(f"keys {code}: {body.decode(errors='replace')[:200]}")
    elif cmd == "open":
        if not args:
            die("open needs an app id, e.g. settings")
        code, body = call("/core/cardputer/action", "POST", {"action": f"open:{args[0]}"})
        if code != 200 or not json.loads(body or b"{}").get("ok", False):
            die(f"open {code}: {body.decode(errors='replace')[:200]}")
    elif cmd in ("volume", "brightness"):
        if not args:
            die(f"{cmd} needs 0-100")
        code, body = call("/core/cardputer/config", "POST", {cmd: int(args[0])})
        print(body.decode() if code == 200 else f"{cmd} {code}: {body.decode()}")
        return 0 if code == 200 else 1
    else:
        die(f"unknown command: {cmd}")

    time.sleep(settle)  # keys are replayed on the device's own loop; let the screen redraw
    print(screen(out, scale))
    return 0


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="See and drive Piblo through Piblo Core.")
    p.add_argument("--core", help="Core base URL (default: from Core's .env)")
    p.add_argument("--out", help="screenshot path (default: %%TEMP%%/piblo-shots/<time>.png)")
    p.add_argument("--scale", type=int, default=3, help="screenshot scale, default 3 (720x405)")
    p.add_argument("--settle", type=float, default=0.8, help="seconds to wait after keys/open before the screenshot")
    p.add_argument("command", nargs="?")
    p.add_argument("operands", nargs="*")
    ns = p.parse_args()
    if ns.core:
        CORE = ns.core.rstrip("/")
    sys.exit(main([ns.command, *ns.operands] if ns.command else [], ns.out, max(1, ns.scale), ns.settle))
