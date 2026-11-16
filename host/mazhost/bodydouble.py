"""Body-double Buddy: a check-in every N minutes, in one of three modes.

glance  voice nudge that names the active window title ("Still on the invoice?")
voice   voice nudge only
silent  no questions; a quiet break buzz every `break_every` intervals
The window title is read on this PC, shown on the device and written to the local ledger.
It is never sent to any model or cloud service.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .buddy import AlertIn
from .ledger import append_ledger

DEFAULTS = {"enabled": False, "mode": "voice", "interval_min": 25, "break_every": 4}


class BodyDoubleIn(BaseModel):
    enabled: bool | None = None
    mode: Literal["glance", "voice", "silent"] | None = None
    interval_min: int | None = Field(default=None, ge=1, le=180)
    break_every: int | None = Field(default=None, ge=1, le=12)


def _active_title() -> str:
    from .flow_routes import foreground_window  # Windows only; tick() catches failures
    return foreground_window()["title"]


class BodyDouble:
    def __init__(self, buddy, path: Path, ledger: Callable[[str, str], object] = append_ledger,
                 title_fn: Callable[[], str] = _active_title, clock: Callable[[], float] = time.time) -> None:
        self.buddy, self.path, self.ledger, self.title_fn, self.clock = buddy, Path(path), ledger, title_fn, clock
        self._lock = threading.Lock()
        self._next_at: float | None = None
        self._count = 0
        self._pending: tuple[str, str] | None = None  # (alert id, question) awaiting an answer
        self._stop = threading.Event()

    def settings(self) -> dict:
        data = dict(DEFAULTS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                data.update({k: stored[k] for k in DEFAULTS if k in stored})
        except (OSError, ValueError):
            pass
        return data

    def update(self, patch: dict) -> dict:
        with self._lock:
            data = self.settings()
            data.update({k: v for k, v in patch.items() if v is not None and k in DEFAULTS})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data), encoding="utf-8")
            self._next_at = None  # any change restarts the countdown
            self._count = 0
            return data

    def _settle_pending(self) -> None:
        if not self._pending:
            return
        aid, question = self._pending
        rec = next((a for a in self.buddy.alerts()["alerts"] if a["id"] == aid), None)
        if rec is None or rec["outcome"] is None:
            answer = "no answer"
        else:
            answer = "yes" if rec["outcome"] == "acked" else "no"
        self._pending = None
        self.ledger("checkin", f"{question} Answer: {answer}.")

    def tick(self, now: float | None = None) -> dict | None:
        """Call about every 30 s. Returns what it did, or None when nothing was due."""
        now = self.clock() if now is None else now
        with self._lock:
            s = self.settings()
            if not s["enabled"]:
                self._next_at = None
                return None
            if self._next_at is None:
                self._next_at = now + s["interval_min"] * 60
                return None
            if now < self._next_at:
                return None
            self._next_at = now + s["interval_min"] * 60
            self._count += 1
            self._settle_pending()
            mode = s["mode"]
            if mode == "silent":
                if self._count % s["break_every"]:
                    return None
                self.buddy.post_alert(AlertIn(source="body_double", kind="info", title="Break time",
                                              priority="low", actions=["ack"]))
                self.ledger("checkin", "Break buzz (silent mode).")
                return {"mode": mode, "posted": "Break time"}
            question = "Still on track?"
            if mode == "glance":
                try:
                    title = " ".join(self.title_fn().split())[:40]
                except Exception:  # no desktop / locked screen: fall back to the plain nudge
                    title = ""
                if title:
                    question = f"Still on {title}?"
            res = self.buddy.post_alert(AlertIn(source="body_double", kind="reminder", title=question[:60],
                                                priority="normal", actions=["ack", "snooze"],
                                                expires_in=s["interval_min"] * 60))
            self._pending = (res["id"], question)  # ack = yes, snooze = no
            return {"mode": mode, "posted": question}

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(30):
                try:
                    self.tick()
                except Exception:
                    pass
        threading.Thread(target=loop, name="body-double", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


def build_bodydouble_router(bd: BodyDouble) -> APIRouter:
    router = APIRouter()

    @router.get("/bodydouble")
    def get_bodydouble():
        return bd.settings()

    @router.patch("/bodydouble")
    def patch_bodydouble(body: BodyDoubleIn):
        return bd.update(body.model_dump(exclude_none=True))

    return router
