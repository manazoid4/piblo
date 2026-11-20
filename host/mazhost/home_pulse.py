"""Home Pulse: one low-risk household integration -> the existing Buddy alert ring.

A signed webhook (synthetic Home Assistant sensor, opt-in parcel webhook or local timer)
becomes a simple human status card with Later/Acknowledge. It can NEVER approve, decide
or execute anything: the only effect is buddy.post_alert of kind=info.

Trust boundary: mounted without the Core bearer (like /pair), so every event must carry
a valid HMAC-SHA256 of the raw body under MAZ_HOME_PULSE_SECRET. Disabled by default.
Replay protection: +/-5 min timestamp window plus a durable event_id ledger. Quiet hours
delay the card instead of dropping it. No new scheduler or alert store is created.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .buddy import MAX_DELAY_S, AlertIn, Buddy
from .config import Settings
from .promises import tz

log = logging.getLogger("uvicorn.error")

PROVIDERS = frozenset({"home-assistant", "parcel", "timer"})
WINDOW_S = 300          # replay window: event ts must be within 5 min of Core's clock


class PulseEvent(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    event_id: str = Field(min_length=1, max_length=70)
    title: str = Field(min_length=1, max_length=60)
    body: str = Field(default="", max_length=200)
    ts: float
    expires_in: int | None = Field(default=None, ge=1, le=MAX_DELAY_S)


def _quiet_delay(quiet: str, now: float | None = None) -> int:
    """Seconds until quiet hours end (0 outside them). 'HH:MM-HH:MM' local, may cross midnight."""
    if not quiet or "-" not in quiet:
        return 0
    try:
        start_s, end_s = quiet.split("-", 1)
        start = datetime.strptime(start_s.strip(), "%H:%M").time()
        end = datetime.strptime(end_s.strip(), "%H:%M").time()
    except ValueError:
        return 0                      # a malformed setting must not silence the house
    local = datetime.fromtimestamp(now if now is not None else time.time(), tz())
    t = local.time()
    inside = start <= t < end if start <= end else (t >= start or t < end)
    if not inside:
        return 0
    end_dt = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if end_dt <= local:
        end_dt += timedelta(days=1)
    return min(int((end_dt - local).total_seconds()) + 1, MAX_DELAY_S)


class _Ledger:
    """Durable event_id dedupe. One small table, pruned to the replay window."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("create table if not exists seen(event_id text primary key, ts real)")
            conn.commit()

    def claim(self, event_id: str, now: float) -> bool:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("delete from seen where ts < ?", (now - WINDOW_S,))
            try:
                conn.execute("insert into seen(event_id, ts) values (?, ?)", (event_id, now))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def reset(self) -> int:
        with closing(sqlite3.connect(self.path)) as conn:
            n = conn.execute("select count(*) from seen").fetchone()[0]
            conn.execute("delete from seen")
            conn.commit()
            return n


def build_home_pulse_app(cfg: Settings, buddy: Buddy, data_dir: Path) -> FastAPI:
    app = FastAPI(title="Piblo Home Pulse")
    ledger = _Ledger(Path(data_dir) / "home_pulse.sqlite3")

    @app.post("/event")
    async def event(request: Request):
        if not cfg.home_pulse_enabled:
            raise HTTPException(503, "home_pulse_disabled")
        secret = cfg.home_pulse_secret
        if not secret:
            raise HTTPException(503, "home_pulse_secret_missing")
        # shortcut: header-based cap only (chunked bodies bypass it); LAN opt-in webhook, upgrade to streaming cap if exposed wider
        if int(request.headers.get("content-length") or 0) > 16384:
            raise HTTPException(413, "event_too_large")
        raw = await request.body()
        sig = request.headers.get("x-pulse-signature", "")
        want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            raise HTTPException(401, "bad_signature")
        try:
            ev = PulseEvent.model_validate_json(raw)
        except ValueError:
            raise HTTPException(422, "invalid_event")
        if ev.provider not in PROVIDERS:
            raise HTTPException(403, "provider_not_allowed")
        now = time.time()
        if abs(now - ev.ts) > WINDOW_S:
            raise HTTPException(403, "stale_event")
        key = f"{ev.provider}:{ev.event_id}"
        if not ledger.claim(key, now):
            return {"ok": True, "deduped": True}
        delay = _quiet_delay(cfg.home_pulse_quiet, now)
        try:
            posted = buddy.post_alert(AlertIn(
                source="home", kind="info", title=ev.title,
                body=(ev.body or ev.provider)[:200], priority="normal",
                actions=["ack", "snooze"], delay_seconds=delay,
                expires_in=ev.expires_in,
                idempotency_key="home:" + hashlib.sha1(key.encode()).hexdigest()[:16]))
        except OverflowError:
            raise HTTPException(429, "alert_table_full")
        return {"ok": True, "id": posted["id"], "scheduled": posted.get("scheduled", False),
                "quiet_delay_s": delay}

    return app


def install_home_pulse_routes(api, cfg: Settings, data_dir: Path) -> None:
    """Owner-only (bearer) status and reset; the webhook itself is the mounted sub-app."""
    ledger = _Ledger(Path(data_dir) / "home_pulse.sqlite3")

    @api.get("/home-pulse/status")
    def home_pulse_status():
        return {"ok": True, "enabled": cfg.home_pulse_enabled,
                "configured": bool(cfg.home_pulse_secret), "providers": sorted(PROVIDERS),
                "quiet": cfg.home_pulse_quiet}

    @api.post("/home-pulse/reset")
    def home_pulse_reset():
        return {"ok": True, "cleared": ledger.reset()}
