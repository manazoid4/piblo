"""The Android app build Core holds for the phone's one-tap update (<root>/phone by default).

scripts/stage_phone_apk.py writes piblo.apk + manifest.json {version_code, version_name, label, sha256, size}.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def phone_dir() -> Path:
    return Path(os.environ.get("MAZ_PHONE_DIR") or Path(__file__).resolve().parents[1] / "phone")


def manifest() -> dict | None:
    """The staged build, or None when nothing usable is staged (missing file, bad manifest, size or hash mismatch)."""
    d = phone_dir()
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        apk = d / "piblo.apk"
        if not (isinstance(m, dict) and isinstance(m.get("version_code"), int) and apk.is_file()):
            return None
        if apk.stat().st_size != m.get("size"):
            return None
        h = hashlib.sha256()
        with apk.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != m.get("sha256"):
            return None
        return {k: m.get(k) for k in ("version_code", "version_name", "label", "sha256", "size", "staged_at")}
    except (OSError, ValueError):
        return None
