#!/usr/bin/env python3
"""Put an Android app build on this PC's Piblo Core, so the phone shows "Update available" (Settings > App update).

  python scripts/stage_phone_apk.py --pr 221            newest green CI build of a pull request (needs gh login)
  python scripts/stage_phone_apk.py --main              newest green CI build on main
  python scripts/stage_phone_apk.py --file app.apk --code 150 [--label "my build"]

Writes <core>/phone/piblo.apk + manifest.json. <core> is --out, else MAZ_PHONE_DIR, else the installed Core
(%LOCALAPPDATA%/Piblo/Core/phone), else this repo's host/phone. Core reads it on every request: no restart.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = "manazoid4/piblo"
WORKFLOW = "Android companion"
ARTIFACT = "piblo-companion-debug"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def out_dir(arg: str | None) -> Path:
    if arg or os.environ.get("MAZ_PHONE_DIR"):
        return Path(arg or os.environ["MAZ_PHONE_DIR"])
    installed = Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "Piblo" / "Core"
    return installed / "phone" if installed.is_dir() else Path(__file__).resolve().parents[1] / "host" / "phone"


def from_ci(branch: str, label: str, tmp: Path) -> tuple[Path, int, str, str]:
    runs = json.loads(gh("run", "list", "-R", REPO, "--workflow", WORKFLOW, "--branch", branch, "--status", "success",
                         "-L", "1", "--json", "databaseId,number,headSha"))
    if not runs:
        sys.exit(f"No green '{WORKFLOW}' build on {branch} yet. Wait for CI, then run this again.")
    run = runs[0]
    gh("run", "download", str(run["databaseId"]), "-R", REPO, "-n", ARTIFACT, "-D", str(tmp))
    info_file = tmp / "piblo-build.json"
    if not info_file.is_file():
        sys.exit("That build is older than one-tap updates (no piblo-build.json). Push a new commit to rebuild it.")
    info = json.loads(info_file.read_text(encoding="utf-8"))
    return tmp / "app-debug.apk", int(info["version_code"]), info["version_name"], f"{label} ({run['headSha'][:7]})"


def stage(apk: Path, code: int, name: str, label: str, dest: Path) -> dict:
    data = apk.read_bytes()
    if data[:2] != b"PK":
        sys.exit(f"{apk} is not an APK")
    dest.mkdir(parents=True, exist_ok=True)
    m = {"version_code": code, "version_name": name, "label": label, "sha256": hashlib.sha256(data).hexdigest(),
         "size": len(data), "staged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    # apk first, manifest last: Core never offers a manifest whose apk is half written (it checks size + hash too)
    tmp_apk = dest / "piblo.apk.part"
    tmp_apk.write_bytes(data)
    os.replace(tmp_apk, dest / "piblo.apk")
    (dest / "manifest.json.part").write_text(json.dumps(m, indent=1), encoding="utf-8")
    os.replace(dest / "manifest.json.part", dest / "manifest.json")
    return m


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pr", type=int)
    g.add_argument("--main", action="store_true")
    g.add_argument("--file")
    p.add_argument("--code", type=int, help="versionCode of --file")
    p.add_argument("--name", default="local build")
    p.add_argument("--label")
    p.add_argument("--out")
    a = p.parse_args()
    with tempfile.TemporaryDirectory() as t:
        if a.file:
            if a.code is None:
                p.error("--file needs --code (the build's versionCode)")
            apk, code, name, label = Path(a.file), a.code, a.name, a.label or Path(a.file).name
        elif a.pr:
            branch = json.loads(gh("pr", "view", str(a.pr), "-R", REPO, "--json", "headRefName"))["headRefName"]
            apk, code, name, label = from_ci(branch, a.label or f"PR #{a.pr}", Path(t))
        else:
            apk, code, name, label = from_ci("main", a.label or "main", Path(t))
        m = stage(apk, code, name, label, out_dir(a.out))
    print(f"Staged {m['label']}: {m['version_name']} (code {m['version_code']}, {m['size'] // 1024} KB) in {out_dir(a.out)}")
    print("On the phone: Settings > App update > Update.")


if __name__ == "__main__":
    main()
