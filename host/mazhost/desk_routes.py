"""GET /desk/state: the small, cheap poll behind the Piblo Desk screen (172x320 thin client).

Every field is read from an existing Core source (Buddy summary/today/status/tasks, Focus). Where Core has
no source the field is null. No model calls, no network, every string already truncated to the device width.
"""
from __future__ import annotations

import json
import time
from typing import Callable

from fastapi import APIRouter, Response

from .buddy import Buddy, fold_phone_text
from .focus import Focus
from .version import CORE_VERSION

STALE_S = 30 * 60        # a running/waiting task not updated for this long is stale (its agent is gone)
DEVICE_FRESH_S = 10 * 60  # a battery report older than this is not shown


def _text(value, limit: int) -> str:
    """Device text: ASCII only (the screen font), trimmed to `limit` characters."""
    raw = " ".join(str(value or "").split())
    return (fold_phone_text(raw) or raw.encode("ascii", "replace").decode())[:limit]


def _agents(store, now: float) -> tuple[dict | None, dict | None]:
    """Counts of live agent tasks, plus the newest live task (None when Core has no task store)."""
    if store is None:
        return None, None
    live = [row for row in store.tasks() if row["status"] in ("running", "waiting")]  # newest first
    fresh = [row for row in live if now - row["updated"] < STALE_S]
    counts = {"working": sum(r["status"] == "running" for r in fresh),
              "waiting": sum(r["status"] == "waiting" for r in fresh),
              "stale": len(live) - len(fresh),
              "latest": _text(fresh[0]["title"], 40) if fresh else ""}
    return counts, (fresh[0] if fresh else None)


def build_desk_router(buddy: Buddy, focus: Focus, clock: Callable[[], float] = time.time) -> APIRouter:
    router = APIRouter(tags=["desk"])

    @router.get("/desk/state")
    def desk_state():
        now = clock()
        summary = buddy.summary(limit=1)
        today = buddy.today()
        sprint = focus.state()
        status = buddy.status()
        agents, current = _agents(buddy.store, now)

        needs = summary["agent"] == "needs_you"
        focus_active = sprint["state"] == "running"
        if needs:
            state, mood = "alert", "worried"
            line = f"{summary['count']} waiting for you"
        elif summary["agent"] == "working":
            state, mood, line = "thinking", "focused" if focus_active else "calm", "Working on it"
        else:
            state = "idle"
            mood = "focused" if focus_active else "happy" if today["done"] else "calm"
            line = (f"Focus: {sprint['task']}" if focus_active
                    else f"{today['done']} done today" if today["done"] else "All quiet")

        device_state = status["device"]
        fresh_device = bool(device_state) and now - device_state.get("ts", 0) < DEVICE_FRESH_S
        body = {
            "ts": int(now),
            "buddy": {"state": state, "mood": mood, "line": _text(line, 40)},
            "task": {"title": _text(current["title"], 40), "project": None} if current else None,
            "focus": {"active": focus_active, "label": _text(sprint.get("task"), 24),
                      "remaining_s": int(sprint.get("remaining_s", 0)),
                      "total_s": int(sprint.get("minutes", 0)) * 60},
            "agents": agents,
            "approvals": {"pending": summary["count"],
                          "top": _text(summary["items"][0]["summary"], 40) if summary["items"] else ""},
            "flow": None,   # Core keeps no record of the last dictation or paste result
            "core": {"ok": True, "version": _text(CORE_VERSION, 16)},
            "device": ({"battery": device_state.get("battery_pct"), "charging": device_state.get("charging"),
                        "app": None} if fresh_device else None),
        }
        return Response(json.dumps(body, separators=(",", ":"), ensure_ascii=False),
                        media_type="application/json", headers={"Cache-Control": "no-store"})

    return router
