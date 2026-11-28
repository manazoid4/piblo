"""GET /craft/agents: which coding agents PibloCraft can show, and how much usage each Claude account has left.
Account data comes from `cswap list --json`; only number, active flag and usage are read (never email or org)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI

from .nowindow import no_window

CACHE_S = 120
TIMEOUT_S = 8
_cache: dict = {"at": 0.0, "value": None}
_lock = threading.Lock()
TAIL_BYTES = 256 * 1024
STALE_S = 7 * 24 * 3600   # older than the weekly window: the numbers say nothing about now


def _left(usage: dict) -> tuple[int, str | None]:
    """% left = 100 - max(5h, 7d) used, like mazcraft acct_left; a window whose reset has passed counts as unused."""
    worst, resets = 0.0, None
    for key in ("fiveHour", "sevenDay"):
        w = (usage or {}).get(key) or {}
        r = w.get("resetsAt")
        try:
            expired = r is not None and datetime.fromisoformat(r.replace("Z", "+00:00")) <= datetime.now(timezone.utc)
        except (ValueError, AttributeError, TypeError):
            expired = False
        pct = 0.0 if expired else float(w.get("pct") or 0)
        if pct >= worst:
            worst, resets = pct, (None if expired else r)
    return max(0, min(100, round(100 - worst))), resets


def _claude_agents() -> list[dict] | None:
    try:
        out = subprocess.run(["cswap", "list", "--json"], capture_output=True, text=True, timeout=TIMEOUT_S, check=True, **no_window()).stdout
        agents = []
        for a in json.loads(out)["accounts"]:
            ok = a.get("usageStatus") == "ok" and a.get("usage")
            left, resets = _left(a["usage"]) if ok else (None, None)
            agents.append({"id": f"claude-{a['number']}", "label": f"Claude {a['number']}", "kind": "claude",
                           "installed": True, "active": bool(a.get("active")), "pct_left": left, "resets_at": resets})
        return agents
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def _newest_codex_log(home: Path) -> Path | None:
    """Newest rollout under sessions/YYYY/MM/DD without walking every old day."""
    days = sorted((d for y in (home / "sessions").glob("[0-9]" * 4) for m in y.glob("[0-9]" * 2) for d in m.glob("[0-9]" * 2)),
                  reverse=True)
    for day in days:
        logs = list(day.glob("*.jsonl"))
        if logs:
            return max(logs, key=lambda p: p.stat().st_mtime)
    return None


def _find_limits(obj) -> dict | None:
    if isinstance(obj, dict):
        if isinstance(obj.get("rate_limits"), dict):
            return obj["rate_limits"]
        for v in obj.values():
            found = _find_limits(v)
            if found:
                return found
    return None


def _codex_usage(now: float | None = None) -> tuple[int | None, str | None, str | None]:
    """(pct_left, resets_at ISO, as_of ISO) from the last rate_limits line Codex logged; (None, None, None) if unknown."""
    now = time.time() if now is None else now
    try:
        log = _newest_codex_log(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
        if log is None or now - log.stat().st_mtime > STALE_S:
            return None, None, None
        with log.open("rb") as f:
            f.seek(max(0, log.stat().st_size - TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
        limits = next((found for line in reversed(lines) if '"rate_limits"' in line
                       for found in [_find_limits(json.loads(line))] if found), None)
        if not limits:
            return None, None, None
        worst, resets = 0.0, None
        for key in ("primary", "secondary"):
            w = limits.get(key) or {}
            r = w.get("resets_at")
            pct = 0.0 if isinstance(r, (int, float)) and r <= now else float(w.get("used_percent") or 0)
            if pct >= worst:
                worst, resets = pct, r if isinstance(r, (int, float)) and r > now else None
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
        return max(0, min(100, round(100 - worst))), (iso(resets) if resets else None), iso(log.stat().st_mtime)
    except (OSError, ValueError, TypeError, AttributeError):
        return None, None, None


def craft_agents() -> dict:
    with _lock:   # one refresh at a time: concurrent first requests must not run cswap twice
        now = time.monotonic()
        if _cache["value"] is None or now - _cache["at"] >= CACHE_S:
            claude = _claude_agents()
            left, resets, as_of = _codex_usage()
            codex = {"id": "codex", "label": "Codex", "kind": "codex", "installed": shutil.which("codex") is not None,
                     "active": False, "pct_left": left, "resets_at": resets, "as_of": as_of}
            value = {"ok": True, "agents": (claude or []) + [codex]}
            if claude is None:
                value["error"] = "cswap_unavailable"
            _cache.update(at=now, value=value)
        return _cache["value"]


def install_craft_agents_routes(api: FastAPI) -> None:
    @api.get("/craft/agents")
    def get_craft_agents():
        return craft_agents()
