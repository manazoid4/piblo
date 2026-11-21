"""GET /craft/agents: which coding agents PibloCraft can show, and how much usage each Claude account has left.
Account data comes from `cswap list --json`; only number, active flag and usage are read (never email or org)."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone

from fastapi import FastAPI

from .nowindow import no_window

CACHE_S = 120
TIMEOUT_S = 8
_cache: dict = {"at": 0.0, "value": None}


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


def craft_agents() -> dict:
    now = time.monotonic()
    if _cache["value"] is None or now - _cache["at"] >= CACHE_S:
        claude = _claude_agents()
        codex = {"id": "codex", "label": "Codex", "kind": "codex", "installed": shutil.which("codex") is not None,
                 "active": False, "pct_left": None, "resets_at": None}
        value = {"ok": True, "agents": (claude or []) + [codex]}
        if claude is None:
            value["error"] = "cswap_unavailable"
        _cache.update(at=now, value=value)
    return _cache["value"]


def install_craft_agents_routes(api: FastAPI) -> None:
    @api.get("/craft/agents")
    def get_craft_agents():
        return craft_agents()
