"""GET /desk/state: the Piblo Desk thin-client poll. Schema, truncation, honest nulls, auth, size cap."""
from __future__ import annotations

import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost.app import create_app
from mazhost.buddy import Buddy, TaskIn
from mazhost.buddy_store import BuddyStore
from mazhost.config import Settings
from mazhost.desk_routes import build_desk_router
from mazhost.focus import Focus

TOKEN = "test-token-that-is-not-default"
H = {"Authorization": f"Bearer {TOKEN}"}
KEYS = {"ts", "buddy", "task", "focus", "agents", "approvals", "flow", "core", "device"}


def bare(tmp_path, buddy=None, clock=time.time):
    buddy = buddy or Buddy(store=BuddyStore(tmp_path / "buddy.sqlite3"))
    focus = Focus(log_file=str(tmp_path / "focus.jsonl"), now_file=str(tmp_path / "NOW.md"), clock=clock)
    api = FastAPI()
    api.include_router(build_desk_router(buddy, focus, clock=clock))
    return TestClient(api), buddy, focus


def test_schema_keys_and_types_when_empty(tmp_path):
    c, _, _ = bare(tmp_path)
    r = c.get("/desk/state")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == KEYS
    assert isinstance(body["ts"], int)
    assert set(body["buddy"]) == {"state", "mood", "line"}
    assert body["buddy"]["state"] in {"idle", "listening", "thinking", "speaking", "alert", "asleep"}
    assert body["buddy"]["mood"] in {"calm", "happy", "focused", "worried"}
    assert set(body["focus"]) == {"active", "label", "remaining_s", "total_s"}
    assert body["focus"]["active"] is False and body["focus"]["remaining_s"] == 0
    assert body["agents"] == {"working": 0, "waiting": 0, "stale": 0, "latest": ""}
    assert body["approvals"] == {"pending": 0, "top": ""}
    assert body["core"] == {"ok": True, "version": body["core"]["version"]} and body["core"]["version"]


def test_nulls_where_core_has_no_source(tmp_path):
    buddy = Buddy()          # no task store, no device report
    c, _, _ = bare(tmp_path, buddy=buddy)
    body = c.get("/desk/state").json()
    assert body["agents"] is None            # no store: counts unknown, not zero
    assert body["task"] is None
    assert body["device"] is None            # nothing ever reported
    assert body["flow"] is None              # Core keeps no last dictation / paste record
    assert body["buddy"]["state"] == "idle" and body["buddy"]["mood"] == "calm"


def test_populated_task_focus_device_and_approval(tmp_path):
    c, buddy, focus = bare(tmp_path)
    buddy.task_update(TaskIn(id="t1", title="Refactor the auth module", status="running", agent="claude"))
    buddy.set_device_state({"battery_pct": 77, "charging": True}, "firmware/1.6.0")
    focus.start(minutes=25, task="Write the intro paragraph for the launch post")
    buddy.request("Send email", "Reply to Ann about the invoice", agent="claude", nod=False)
    body = c.get("/desk/state").json()
    assert body["task"] == {"title": "Refactor the auth module", "project": None}
    assert body["agents"] == {"working": 1, "waiting": 0, "stale": 0, "latest": "Refactor the auth module"}
    assert body["focus"]["active"] is True and body["focus"]["label"] == "Write the intro paragrap"
    assert 1490 <= body["focus"]["remaining_s"] <= 1500 and body["focus"]["total_s"] == 1500
    assert body["device"] == {"battery": 77, "charging": True, "app": None}
    assert body["approvals"]["pending"] == 1 and body["approvals"]["top"]
    assert body["buddy"]["state"] == "alert" and body["buddy"]["mood"] == "worried"
    assert body["buddy"]["line"] == "1 waiting for you"


def test_long_strings_are_truncated_and_ascii(tmp_path):
    c, buddy, _ = bare(tmp_path)
    buddy.task_update(TaskIn(id="t2", title="Café — " + "x" * 50, status="waiting", agent="a"))
    body = c.get("/desk/state").json()
    assert len(body["task"]["title"]) <= 40 and body["task"]["title"].isascii()
    assert len(body["agents"]["latest"]) <= 40
    assert body["agents"]["waiting"] == 1
    assert len(body["buddy"]["line"]) <= 40


def test_stale_tasks_are_counted_apart_from_live_ones(tmp_path):
    now = [time.time()]
    c, buddy, _ = bare(tmp_path, clock=lambda: now[0])
    buddy.task_update(TaskIn(id="old", title="Dead agent", status="running", agent="x"))
    now[0] += 3600
    body = c.get("/desk/state").json()
    assert body["agents"] == {"working": 0, "waiting": 0, "stale": 1, "latest": ""}
    assert body["task"] is None


def test_stale_device_report_is_not_shown(tmp_path):
    now = [time.time()]
    c, buddy, _ = bare(tmp_path, clock=lambda: now[0])
    buddy.set_device_state({"battery_pct": 50}, "firmware/1.6.0")
    now[0] += 3600
    assert c.get("/desk/state").json()["device"] is None


def test_size_cap_with_worst_case_strings(tmp_path):
    c, buddy, focus = bare(tmp_path)
    buddy.task_update(TaskIn(id="t3", title="W" * 60, status="running", agent="a"))
    focus.start(minutes=99, task="F" * 200)
    buddy.set_device_state({"battery_pct": 100, "charging": True}, "firmware/" + "9" * 40)
    for _ in range(3):
        buddy.request("T" * 60, "S" * 300, agent="a" * 60, nod=False)
    r = c.get("/desk/state")
    assert len(r.content) < 800
    assert json.loads(r.content)["core"]["version"]


def test_auth_required_like_other_core_routes(tmp_path):
    api = create_app(Settings(token=TOKEN, buddy_db=str(tmp_path / "b.sqlite3"), _env_file=None))
    c = TestClient(api)
    assert c.get("/desk/state").status_code == 401
    assert c.get("/desk/state", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = c.get("/desk/state", headers=H)
    assert ok.status_code == 200 and set(ok.json()) == KEYS


def test_existing_buddy_routes_unaffected(tmp_path):
    api = create_app(Settings(token=TOKEN, buddy_db=str(tmp_path / "b.sqlite3"), _env_file=None))
    c = TestClient(api)
    assert c.get("/buddy/summary", headers=H).json()["agent"] == "idle"
    assert c.get("/buddy/pending", headers=H).status_code == 200
    assert c.get("/core/device", headers=H).status_code == 200
    spec = c.get("/openapi.json").json()["paths"]
    assert "/desk/state" in spec and "/buddy/state" in spec


def test_pairing_approval_is_required_for_desk_state(tmp_path):
    api = create_app(Settings(token=TOKEN, buddy_db=str(tmp_path / 'b.sqlite3'), _env_file=None))
    c = TestClient(api, client=('192.168.1.50', 50000))
    denied = c.post('/pair/device/request', json={'device_id': 'piblo-desk1', 'name': 'Piblo Desk'}).json()
    assert c.get('/desk/state').status_code == 401
    assert c.post(f"/pair/device/{denied['request_id']}/deny", headers=H).status_code == 200
    assert c.get(f"/pair/device/request/{denied['request_id']}").json() == {'status': 'denied'}
    assert c.get('/desk/state').status_code == 401
    pending = c.post('/pair/device/request', json={'device_id': 'piblo-desk1', 'name': 'Piblo Desk'}).json()
    assert c.get(f"/pair/device/request/{pending['request_id']}").json() == {'status': 'pending'}
    assert c.post(f"/pair/device/{pending['request_id']}/approve", headers=H).status_code == 200
    issued = c.get(f"/pair/device/request/{pending['request_id']}").json()['token']
    assert c.get('/desk/state', headers={'Authorization': f'Bearer {issued}'}).status_code == 200
