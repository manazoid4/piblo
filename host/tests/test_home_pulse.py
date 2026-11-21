"""Home Pulse: signed household events -> existing Buddy alerts. All payloads synthetic."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from mazhost import buddy as buddy_mod
from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.home_pulse import _quiet_delay
from mazhost.promises import tz

TOKEN = "test-t...ault"
H = {"Authorization": f"Bearer {TOKEN}"}
PULSE_KEY = "synthetic-pulse-key"


def sign(raw: bytes, secret: str = PULSE_KEY) -> dict:
    return {"X-Pulse-Signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
            "Content-Type": "application/json"}


def ev(event_id="door-1", provider="home-assistant", title="Front door open", ts=None, **kw):
    return {"provider": provider, "event_id": event_id, "title": title,
            "ts": time.time() if ts is None else ts, **kw}


def post(client, payload, secret=PULSE_KEY):
    raw = json.dumps(payload).encode()
    return client.post("/home/pulse/event", content=raw, headers=sign(raw, secret))


def api(tmp_path, **kw):
    # quiet off by default so tests are time-of-day independent; quiet test sets its own window
    opts = dict(token=TOKEN, _env_file=None, data_dir=str(tmp_path),
                home_pulse_enabled=True, home_pulse_secret=PULSE_KEY, home_pulse_quiet="")
    opts.update(kw)
    return TestClient(create_app(Settings(**opts)))


def fired_ids(client):
    return [e["alert"]["id"] for e in client.get("/buddy/events", headers=H).json()["events"]
            if e["kind"] == "alert"]


def test_signed_sensor_event_becomes_alert_with_one_button_actions(tmp_path):
    c = api(tmp_path)
    r = post(c, ev(body="sensor.front_door = on"))
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert r.json()["scheduled"] is False and r.json()["quiet_delay_s"] == 0
    assert fired_ids(c) == [aid]
    alert = next(a for a in c.get("/buddy/alerts", headers=H).json()["alerts"] if a["id"] == aid)
    assert alert["source"] == "home" and alert["kind"] == "info"
    assert alert["actions"] == ["ack", "snooze"]          # Later / Acknowledge, nothing privileged
    assert c.post(f"/buddy/alert/{aid}/ack", headers=H).json()["outcome"] == "acked"


def test_forged_or_missing_signature_is_rejected(tmp_path):
    c = api(tmp_path)
    assert post(c, ev(), secret="wrong-secret").status_code == 401
    raw = json.dumps(ev(event_id="door-2")).encode()
    assert c.post("/home/pulse/event", content=raw,
                  headers={"Content-Type": "application/json"}).status_code == 401
    assert fired_ids(c) == []


def test_unknown_provider_is_rejected(tmp_path):
    c = api(tmp_path)
    assert post(c, ev(provider="evil-bot")).status_code == 403
    assert fired_ids(c) == []


def test_replayed_event_is_deduped(tmp_path):
    c = api(tmp_path)
    first = post(c, ev(event_id="parcel-42", provider="parcel", title="Parcel delivered"))
    again = post(c, ev(event_id="parcel-42", provider="parcel", title="Parcel delivered"))
    assert first.status_code == 200 and again.json() == {"ok": True, "deduped": True}
    assert fired_ids(c) == [first.json()["id"]]


def test_stale_event_rejected_out_of_order_events_delivered(tmp_path):
    c = api(tmp_path)
    now = time.time()
    assert post(c, ev(event_id="old", ts=now - 400)).status_code == 403
    newer = post(c, ev(event_id="a", ts=now - 50))         # arrives first, happened second
    older = post(c, ev(event_id="b", ts=now - 200))        # out of order, still inside the window
    assert newer.status_code == 200 and older.status_code == 200
    assert set(fired_ids(c)) == {newer.json()["id"], older.json()["id"]}


def test_expired_alert_never_refires_after_snooze(tmp_path, monkeypatch):
    c = api(tmp_path)
    r = post(c, ev(event_id="door-exp", expires_in=10))
    aid = r.json()["id"]
    assert r.status_code == 200 and fired_ids(c) == [aid]   # fires once while valid
    c.post(f"/buddy/alert/{aid}/snooze", headers=H, json={"minutes": 5})
    real = buddy_mod.time.time
    monkeypatch.setattr(buddy_mod.time, "time", lambda: real() + 400)
    assert fired_ids(c) == [aid]                            # expiry beats the snooze re-nag


def test_quiet_hours_delay_instead_of_drop(tmp_path):
    c = api(tmp_path, home_pulse_quiet="00:00-23:59")
    r = post(c, ev(event_id="quiet-1"))
    assert r.status_code == 200 and r.json()["scheduled"] is True
    assert r.json()["quiet_delay_s"] > 0
    assert fired_ids(c) == []                              # waits for morning, not lost


def test_disabled_or_unconfigured_fails_closed(tmp_path):
    off = api(tmp_path, home_pulse_enabled=False)
    assert post(off, ev()).status_code == 503
    nokey = api(tmp_path, home_pulse_secret="")
    assert post(nokey, ev()).status_code == 503
    assert fired_ids(off) == [] and fired_ids(nokey) == []


def test_oversized_body_is_refused_before_signature_check(tmp_path):
    c = api(tmp_path)
    r = c.post("/home/pulse/event", content=b"x" * 20000,
               headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_owner_status_and_reset_are_bearer_protected(tmp_path):
    c = api(tmp_path)
    assert c.get("/home-pulse/status").status_code == 401
    assert c.post("/home-pulse/reset").status_code == 401
    status = c.get("/home-pulse/status", headers=H)
    assert status.status_code == 200
    assert status.json()["enabled"] is True and status.json()["configured"] is True
    assert PULSE_KEY not in status.text                       # never echo the key
    post(c, ev(event_id="reset-1"))
    assert c.post("/home-pulse/reset", headers=H).json()["cleared"] >= 1
    assert post(c, ev(event_id="reset-1")).json().get("deduped") is not True   # ledger cleared


def test_injected_privileged_fields_are_ignored(tmp_path):
    c = api(tmp_path)
    r = post(c, ev(event_id="inj", kind="approval", actions=["approve"], sensitive=True))
    assert r.status_code == 200
    alert = next(a for a in c.get("/buddy/alerts", headers=H).json()["alerts"] if a["id"] == r.json()["id"])
    assert alert["kind"] == "info" and alert["actions"] == ["ack", "snooze"]


def test_quiet_delay_maths():
    at = lambda h, m: datetime(2027, 1, 4, h, m, tzinfo=tz()).timestamp()
    assert _quiet_delay("22:00-07:00", at(22, 30)) == 8 * 3600 + 30 * 60 + 1
    assert _quiet_delay("22:00-07:00", at(8, 0)) == 0
    assert _quiet_delay("22:00-07:00", at(6, 59)) == 61
    assert _quiet_delay("", at(23, 0)) == 0
    assert _quiet_delay("not-a-window", at(23, 0)) == 0    # malformed never silences
