#!/usr/bin/env python3
"""Flash a Piblo firmware build: piblo_flash.py --auto [--bin path] [--usb] [--allow-branch] --yes

Default = OTA over Wi-Fi: stage the bin into Core's fw dir (scripts/stage_fw.py guards, or
scripts/fw_manifest.py with --allow-branch for dev builds), then POST Core's existing
/core/cardputer/action "fwcheck" so the device checks for updates now. --usb = scripts/piblo_usb_update.py
(MAZOTA over serial). Afterwards waits for the device and confirms the version from Core status.
Without --yes it only prints the plan. Refuses same/older versions unless --force.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

import _common as c

REPO = pathlib.Path(__file__).resolve().parents[1]
LIMIT = 1507328  # app slot, same constant as scripts/stage_fw.py
VER = re.compile(rb"(?:nod|Piblo) v(\d+\.\d+(?:\.\d+)?) \(([0-9a-f]+|nogit)\)\nCore")


def check_bin(data):
    """-> (version, sha). Raises ValueError if this is not a Piblo app image that fits the slot."""
    m = VER.search(data)
    if not m:
        raise ValueError("no Piblo version string in bin")
    if data[:1] != b"\xe9":
        raise ValueError("not an ESP32 app image (first byte != 0xE9)")
    if len(data) >= LIMIT:
        raise ValueError(f"{len(data)} B does not fit the {LIMIT} B slot")
    return m[1].decode(), m[2].decode()


def vtuple(v):
    return tuple(int(p) for p in v.split("."))


def default_fw_dir():
    core = os.environ.get("NOD_CORE_DIR") or pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Piblo" / "Core"
    return pathlib.Path(core) / "fw"


def run(*cmd):
    r = subprocess.run([sys.executable, *map(str, cmd)], cwd=REPO)
    if r.returncode:
        sys.exit(f"error: {pathlib.Path(cmd[0]).name} failed ({r.returncode})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--auto", action="store_true", help="use defaults (build output, Core fw dir)")
    ap.add_argument("--bin", default=str(REPO / ".pio/build/cardputer-adv/firmware.bin"))
    ap.add_argument("--usb", action="store_true", help="flash over USB serial instead of Wi-Fi OTA")
    ap.add_argument("--allow-branch", action="store_true", help="dev: stage a non-origin/main build")
    ap.add_argument("--force", action="store_true", help="allow same/older version")
    ap.add_argument("--fw-dir", default=str(default_fw_dir()), help="Core's fw dir (default: installed Core)")
    ap.add_argument("--wait", type=int, default=240, help="seconds to wait for the new version")
    ap.add_argument("--yes", action="store_true", help="actually flash (otherwise print the plan only)")
    a = ap.parse_args(argv)
    c.need_token()
    data = pathlib.Path(a.bin).read_bytes()
    try:
        target, sha = check_bin(data)
    except ValueError as e:
        sys.exit(f"REFUSED: {e}")
    st = c.status()
    running = st.get("version") if st else None
    print(f"plan: {'USB' if a.usb else 'OTA'} {running or 'device offline'} -> {target} ({sha}) "
          f"from {a.bin} via {a.fw_dir}")
    if running and vtuple(target) <= vtuple(running) and not a.force:
        sys.exit("REFUSED: image is not newer than the device (use --force)")
    if not a.yes:
        print("dry run: add --yes to flash")
        return
    if not st and not a.usb:
        sys.exit("error: device offline; OTA needs Wi-Fi (try --usb)")

    stager = ["scripts/fw_manifest.py", a.bin, a.fw_dir] if a.allow_branch else ["scripts/stage_fw.py", "--bin", a.bin, a.fw_dir]
    run(*stager)
    if a.usb:
        run("scripts/piblo_usb_update.py", "--bin", str(pathlib.Path(a.fw_dir) / "latest.bin"), *(["--force"] if a.force else []))
    else:
        code, body = c.pr.call("/core/cardputer/action", "POST", {"action": "fwcheck"})
        print(f"fwcheck -> {code} {body.decode(errors='replace')[:120]}")
        if code not in (200, 202):
            sys.exit("error: Core could not tell the device to check for updates")
    end, seen_down = time.time() + a.wait, False
    while time.time() < end:
        time.sleep(3)
        s = c.status()
        seen_down |= s is None
        if s and s.get("version") == target:
            print(f"confirmed: device reports v{target} (rebooted={seen_down})")
            return
    sys.exit(f"not confirmed: device still reports {(c.status() or {}).get('version')} after {a.wait}s "
             "(it may be waiting for the update prompt on screen)")


if __name__ == "__main__":
    main()
