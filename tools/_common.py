"""Shared helpers for tools/*: thin wrappers over scripts/piblo_remote.py (Core URL/token, screen PNG, keys)."""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import piblo_remote as pr  # noqa: E402  (reused: token discovery, call(), png encoder)

ESP32S3_VID = 0x303A  # Espressif; Cardputer ADV enumerates as 303A:1001 (USB-Serial/JTAG)


def is_esp_port(vid):
    return vid == ESP32S3_VID


def esp_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [p.device for p in list_ports.comports() if is_esp_port(p.vid)]


def status():
    """Device status dict from Core, or None if Core/device is unreachable. Never exits."""
    try:
        code, body = pr.call("/core/cardputer/status")
        d = json.loads(body)
    except (SystemExit, ValueError):
        return None
    return d if code == 200 and "version" in d else None


def png_bytes():
    """Fresh 240x135 LCD PNG (1x) straight from Core. Raises RuntimeError if no device."""
    code, body = pr.call("/core/cardputer/screen.png")
    if code != 200:
        raise RuntimeError(f"screen {code}: {body.decode(errors='replace')[:120]}")
    return body


def _ok(what, code, body):
    if code != 200 or not json.loads(body or b"{}").get("ok", False):
        raise RuntimeError(f"{what} {code}: {body.decode(errors='replace')[:120]}")


def send_keys(spec):
    _ok("keys", *pr.call("/core/cardputer/key", "POST", {"keys": spec}))


def open_app(app):
    _ok("open", *pr.call("/core/cardputer/action", "POST", {"action": f"open:{app}"}))


def need_token():
    if not pr.TOKEN:
        sys.exit("error: no Piblo token found (install Piblo app or set PIBLO_TOKEN)")


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")
