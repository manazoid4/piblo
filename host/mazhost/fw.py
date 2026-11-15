"""Firmware the Core currently holds for over-the-air updates (<root>/fw by default)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mazhost.fw")


def fw_dir() -> Path:
    return Path(os.environ.get("MAZ_FW_DIR") or Path(__file__).resolve().parents[1] / "fw")


def manifest() -> dict | None:
    """{version, sha, sha256, size} written by scripts/fw_manifest.py; None when no firmware is staged."""
    try:
        data = json.loads((fw_dir() / "manifest.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("version") and (fw_dir() / "latest.bin").is_file() else None
    except (OSError, ValueError):
        return None


def staged_status(running_version: str = "") -> dict:
    """Read-only, verified staging inventory; unknown/equal/older versions fail closed."""
    empty = {"staged": None, "update_available": False}
    staged = manifest()
    if not staged:
        return empty

    def version(value):
        if not isinstance(value, str) or len(value) > 32 or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
            return None
        parts = tuple(map(int, value.split(".")))
        return parts + (0,) * (3 - len(parts))

    latest = version(staged.get("version"))
    running = version(running_version)
    size, digest = staged.get("size"), staged.get("sha256")
    if (latest is None or type(size) is not int or size <= 0 or
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
        return empty
    try:
        binary = fw_dir() / "latest.bin"
        with binary.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            if stat.st_size != size:
                return empty
            checksum = hashlib.sha256()
            for chunk in iter(lambda: stream.read(65536), b""):
                checksum.update(chunk)
        if checksum.hexdigest() != digest.lower():
            return empty
        staged_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return empty
    return {"staged": {k: staged.get(k) for k in ("version", "sha", "sha256", "size")} | {"staged_at": staged_at},
            "update_available": running is not None and latest > running}


def report_path() -> Path:
    return fw_dir() / "last_report.json"


def save_report(report: dict) -> dict:
    """Keep the device's last OTA report (success or failure) readable at GET /core/update."""
    report = {**report, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        fw_dir().mkdir(parents=True, exist_ok=True)
        report_path().write_text(json.dumps(report), encoding="utf-8")
    except OSError:
        pass  # logged below either way; the file is a convenience
    log.warning("fw report: stage=%s error=%r code=%s slot=%s next=%s size=%s build=%s state=%s otadata=%s battery=%s",
                report.get("stage"), report.get("error"), report.get("code"), report.get("slot"),
                report.get("next_slot"), report.get("size"), report.get("build"), report.get("ota_state"),
                report.get("otadata"), report.get("battery"))
    return report


def last_report() -> dict | None:
    try:
        return json.loads(report_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
