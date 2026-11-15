"""Persistent Buddy store: write-through, restart safety, idempotency, corruption, retention, tasks."""
from __future__ import annotations

import pytest

import sqlite3
import time

from fastapi.testclient import TestClient

from mazhost import buddy as buddy_mod
from mazhost.app import create_app
from mazhost.buddy_store import BuddyStore, ROW_CAPS, SCHEMA_VERSION
from mazhost.config import Settings


@pytest.fixture(autouse=True)
def legacy_unsigned_device(monkeypatch):
    # These existing tests exercise the current unsigned simulator/firmware path.
    monkeypatch.setenv("MAZ_ASK_LEGACY_DEVICE_DECIDE", "1")

TOKEN = "test-token-that-is-not-default"
H = {"Authorization": f"Bearer {TOKEN}"}


def boot(tmp_path):
    """One 'Core process': a fresh app on the same DB file."""
    return TestClient(create_app(Settings(token=TOKEN, buddy_db=str(tmp_path / "buddy.sqlite3"), _env_file=None)))


def test_schema_version_and_wal(tmp_path):
    store = BuddyStore(tmp_path / "s.sqlite3")
    assert store.db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    BuddyStore(tmp_path / "s.sqlite3")  # reopening migrates nothing and does not fail
    assert store.db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == SCHEMA_VERSION


def test_history_survives_restart_and_event_ids_keep_rising(tmp_path):
    c = boot(tmp_path)
    c.post("/buddy/event", json={"kind": "done", "text": "build ok", "source": "claude", "agent": "claude"}, headers=H)
    c.post("/buddy/alert", json={"title": "Stand up", "priority": "high", "agent": "cal"}, headers=H)
    first_last = c.get("/buddy/events", headers=H).json()["last"]
    c2 = boot(tmp_path)   # restart
    h = c2.get("/buddy/history", headers=H).json()
    assert [n["text"] for n in h["notifications"]][:2] == ["Stand up", "build ok"]
    assert h["alerts"][0]["title"] == "Stand up" and h["alerts"][0]["outcome"] is None
    # a polling device with since=first_last must still see NEW events after the restart
    c2.post("/buddy/event", json={"kind": "done", "text": "after"}, headers=H)
    evs = c2.get(f"/buddy/events?since={first_last}", headers=H).json()["events"]
    assert [e["text"] for e in evs] == ["after"] and evs[0]["id"] > first_last
    # reloaded alerts are history: they are not re-announced
    assert all(e["kind"] != "alert" for e in c2.get("/buddy/events", headers=H).json()["events"])


def test_pending_approval_is_expired_after_restart_never_replayed(tmp_path):
    c = boot(tmp_path)
    rid = c.post("/buddy/request", json={"tool": "Bash", "summary": "rm -rf build", "agent": "claude"}, headers=H).json()["id"]
    assert c.get("/buddy/summary", headers=H).json()["count"] == 1
    c2 = boot(tmp_path)
    assert c2.get("/buddy/summary", headers=H).json()["count"] == 0
    assert c2.get("/buddy/pending", headers=H).json()["pending"] == []
    st = c2.get(f"/buddy/state?id={rid}", headers=H).json()
    assert st["decision"] == "cancel" and st["expired"] is True      # a waiting hook sees 'not approved'
    assert c2.post("/buddy/decide", json={"id": rid, "decision": "allow"}, headers=H).status_code == 404
    row = [a for a in c2.get("/buddy/history", headers=H).json()["approvals"] if a["id"] == rid][0]
    assert row["decision"] == "expired" and row["agent"] == "claude"


def test_decision_is_persisted_with_owner_and_first_answer_wins(tmp_path):
    c = boot(tmp_path)
    rid = c.post("/buddy/request", json={"tool": "Edit", "agent": "codex", "sensitive": True}, headers=H).json()["id"]
    c.post("/buddy/decide", json={"id": rid, "decision": "allow"}, headers={**H, "X-Piblo-Client": "simulator/1"})
    c.post("/buddy/decide", json={"id": rid, "decision": "deny"}, headers=H)
    row = boot(tmp_path).get("/buddy/history", headers=H).json()["approvals"][0]
    assert (row["decision"], row["agent"], row["decided_by"], row["sensitive"]) == ("allow", "codex", "simulator/1", 1)


def test_approval_timeout_is_recorded_as_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(buddy_mod, "TIMEOUT_S", 0.05)
    c = boot(tmp_path)
    rid = c.post("/buddy/request", json={"tool": "Bash"}, headers=H).json()["id"]
    time.sleep(0.1)
    assert c.get(f"/buddy/state?id={rid}", headers=H).json()["expired"] is True
    assert c.post("/buddy/decide", json={"id": rid, "decision": "allow"}, headers=H).status_code == 409
    assert boot(tmp_path).get("/buddy/history", headers=H).json()["approvals"][0]["decision"] == "expired"


def test_idempotency_key_dedups_approvals_alerts_and_events(tmp_path):
    c = boot(tmp_path)
    a = c.post("/buddy/request", json={"tool": "Bash", "idempotency_key": "k1"}, headers=H).json()
    b = c.post("/buddy/request", json={"tool": "Bash", "idempotency_key": "k1"}, headers=H).json()
    assert a["id"] == b["id"] and b["duplicate"] is True
    x = c.post("/buddy/alert", json={"title": "once", "idempotency_key": "k2"}, headers=H).json()
    y = c.post("/buddy/alert", json={"title": "once", "idempotency_key": "k2"}, headers=H).json()
    assert x["id"] == y["id"] and y["duplicate"] is True
    c.post("/buddy/event", json={"kind": "done", "text": "e", "idempotency_key": "k3"}, headers=H)
    c.post("/buddy/event", json={"kind": "done", "text": "e", "idempotency_key": "k3"}, headers=H)
    c2 = boot(tmp_path)   # restart: the old key stays dead, it is not resurrected as pending
    again = c2.post("/buddy/request", json={"tool": "Bash", "idempotency_key": "k1"}, headers=H).json()
    assert again["id"] == a["id"] and again["decision"] == "cancel"
    h = c2.get("/buddy/history", headers=H).json()
    assert len(h["approvals"]) == 1 and len(h["alerts"]) == 1
    assert [n["text"] for n in h["notifications"]].count("e") == 1


def test_corrupt_db_is_moved_aside_and_core_starts_fresh(tmp_path):
    path = tmp_path / "buddy.sqlite3"
    path.write_bytes(b"this is not a sqlite database" * 50)
    store = BuddyStore(path)
    assert store.recovered_from == "buddy.sqlite3.corrupt-1"
    assert (tmp_path / "buddy.sqlite3.corrupt-1").read_bytes().startswith(b"this is not")
    assert store.approvals() == []
    assert any(a["kind"] == "store_recovered" for a in store.activity())
    store.close()
    path.write_bytes(b"garbage again" * 50)      # a second corruption never overwrites the first quarantine
    second = BuddyStore(path)
    assert second.recovered_from == "buddy.sqlite3.corrupt-2"
    second.close()
    c = boot(tmp_path)                           # and Core itself works on the recovered file
    assert c.post("/buddy/alert", json={"title": "ok"}, headers=H).status_code == 200


def test_newer_schema_is_refused_not_downgraded(tmp_path):
    path = tmp_path / "buddy.sqlite3"
    BuddyStore(path).close()
    db = sqlite3.connect(path)
    db.execute("INSERT INTO schema_version VALUES (999)")
    db.commit()
    db.close()
    store = BuddyStore(path)
    assert store.recovered_from is not None      # moved aside, never mangled in place
    assert (tmp_path / "buddy.sqlite3.corrupt-1").exists()


def test_failing_store_never_breaks_a_live_approval(tmp_path):
    c = boot(tmp_path)
    closure = [r for r in c.app.routes if getattr(r, "path", "") == "/buddy/request"][0].endpoint.__closure__
    store = next(cell.cell_contents for cell in closure if isinstance(cell.cell_contents, buddy_mod.Buddy)).store
    store.db.close()   # simulate a dead disk
    rid = c.post("/buddy/request", json={"tool": "Bash"}, headers=H).json()["id"]
    assert c.get("/buddy/summary", headers=H).json()["count"] == 1
    assert c.post("/buddy/decide", json={"id": rid, "decision": "deny"}, headers=H).json()["decision"] == "deny"


def test_retention_age_and_row_caps(tmp_path):
    store = BuddyStore(tmp_path / "s.sqlite3")
    old = time.time() - 40 * 86400
    store.db.execute("INSERT INTO activity(ts, level, kind, message) VALUES (?, 'info', 'old', 'x')", (old,))
    store.db.execute("INSERT INTO approvals(id, tool, created, decision) VALUES ('p', 'Bash', ?, NULL)", (old,))
    store.db.execute("INSERT INTO approvals(id, tool, created, decision) VALUES ('d', 'Bash', ?, 'allow')", (old,))
    store.set_pref("retention_days", 30)
    for i in range(ROW_CAPS["activity"] + 20):
        store.log_activity("info", "spam", str(i))
    store.prune()
    kinds = [a["kind"] for a in store.activity(1000)]
    assert "old" not in kinds and len(kinds) == ROW_CAPS["activity"]
    assert [a["id"] for a in store.approvals()] == ["p"]    # age pruning only removes decided approvals


def test_tasks_report_progress_and_raise_one_alert_when_finished(tmp_path):
    c = boot(tmp_path)
    t = {"id": "t1", "title": "Refactor auth", "status": "running", "progress": 40, "agent": "claude"}
    assert c.post("/buddy/task", json=t, headers=H).json()["task"]["progress"] == 40
    assert c.get("/buddy/events", headers=H).json()["events"] == []        # progress alone is quiet
    for _ in range(2):                                                      # a repeated 'done' alerts once
        c.post("/buddy/task", json={**t, "status": "done", "progress": 100}, headers=H)
    alerts = [e["alert"] for e in c.get("/buddy/events", headers=H).json()["events"]]
    assert [(a["kind"], a["title"]) for a in alerts] == [("task_done", "Refactor auth")]
    c.post("/buddy/task", json={"id": "t2", "title": "Deploy", "status": "failed", "detail": "exit 1"}, headers=H)
    failed = c.get("/buddy/events", headers=H).json()["events"][-1]["alert"]
    assert (failed["kind"], failed["priority"], failed["title"]) == ("info", "high", "Failed: Deploy")
    tasks = boot(tmp_path).get("/buddy/tasks", headers=H).json()["tasks"]   # survives restart
    assert {x["id"]: x["status"] for x in tasks} == {"t1": "done", "t2": "failed"}
    assert c.post("/buddy/task", json={**t, "status": "bogus"}, headers=H).status_code == 422
    assert c.post("/buddy/task", json={**t, "progress": 101}, headers=H).status_code == 422


def test_status_tracks_client_and_agents_are_profiled(tmp_path):
    c = boot(tmp_path)
    assert c.get("/buddy/status", headers=H).json()["connected"] is False
    c.get("/buddy/events", headers={**H, "X-Piblo-Client": "simulator/1"})
    s = c.get("/buddy/status", headers=H).json()
    assert (s["connected"], s["simulated"], s["client"]) == (True, True, "simulator/1")
    c.get("/buddy/events", headers={**H, "User-Agent": "ESP32HTTPClient"})
    assert c.get("/buddy/status", headers=H).json()["simulated"] is False
    c.post("/buddy/device-state", json={"battery_pct": 81, "wifi_rssi": -55}, headers={**H, "X-Piblo-Client": "simulator/1"})
    assert c.get("/buddy/status", headers=H).json()["device"]["battery_pct"] == 81
    assert c.post("/buddy/device-state", json={"battery_pct": 400}, headers=H).status_code == 422
    c.post("/buddy/request", json={"tool": "Bash", "agent": "codex"}, headers=H)
    assert "codex" in [a["name"] for a in c.get("/buddy/agents", headers=H).json()["agents"]]
    assert any(r["kind"] == "simulator" for r in BuddyStore(tmp_path / "buddy.sqlite3").connections())


def test_phone_connection_does_not_impersonate_piblo_device(tmp_path):
    c = boot(tmp_path)
    c.get("/buddy/events", headers={**H, "X-Piblo-Client": "android/0.1.0"})
    only_phone = c.get("/buddy/status", headers=H).json()
    assert only_phone["connected"] is False
    assert only_phone["client"] is None
    c.get("/buddy/events", headers={**H, "X-Piblo-Client": "simulator/1"})
    assert c.get("/buddy/status", headers=H).json()["simulated"] is True
    c.get("/buddy/events", headers={**H, "X-Piblo-Client": "firmware/1"})
    real = c.get("/buddy/status", headers=H).json()
    assert (real["connected"], real["simulated"], real["client"]) == (True, False, "firmware/1")


def test_prefs_export_reset_need_token_and_export_has_no_secrets(tmp_path):
    c = boot(tmp_path)
    for method, path in (("get", "/buddy/export"), ("post", "/buddy/reset"), ("get", "/buddy/prefs"), ("get", "/buddy/history")):
        assert getattr(c, method)(path).status_code == 401
    sc = [{"name": "Lunch", "title": "Lunch time", "body": "", "priority": "low"}]
    assert c.put("/buddy/prefs", json={"retention_days": 7, "shortcuts": sc}, headers=H).json()["retention_days"] == 7
    assert c.put("/buddy/prefs", json={"retention_days": 0}, headers=H).status_code == 422
    assert boot(tmp_path).get("/buddy/prefs", headers=H).json()["shortcuts"] == sc
    c.post("/buddy/alert", json={"title": "keep me"}, headers=H)
    exported = c.get("/buddy/export", headers=H)
    assert exported.headers["content-disposition"].startswith("attachment")
    assert TOKEN not in exported.text and "keep me" in exported.text
    assert c.post("/buddy/reset", json={}, headers=H).status_code == 400
    assert c.post("/buddy/reset", json={"confirm": True}, headers=H).json()["ok"] is True
    h = c.get("/buddy/history", headers=H).json()
    assert h["alerts"] == [] and h["approvals"] == [] and "keep me" not in c.get("/buddy/export", headers=H).text
    assert c.get("/buddy/prefs", headers=H).json()["shortcuts"] == sc           # prefs survive a history reset
    c.post("/buddy/event", json={"kind": "done", "text": "next"}, headers=H)     # ids still rise
    assert c.get("/buddy/events", headers=H).json()["events"][-1]["id"] >= 2


def test_connect_info_has_ready_configs_without_a_token(tmp_path):
    info = boot(tmp_path).get("/buddy/connect-info", headers=H).json()
    assert info["claude_code_cmd"].startswith("claude mcp add piblo")
    assert "[mcp_servers.piblo]" in info["codex_toml"] and "dist/index.js" in info["codex_toml"]
    assert TOKEN not in str(info)


def test_approval_storage_cannot_reverse_first_decision(tmp_path):
    store = BuddyStore(tmp_path / "s.sqlite3")
    store.add_approval({"id": "immutable", "tool": "Deploy", "t": time.time()})
    store.set_approval_decision("immutable", "allow", "device")
    store.set_approval_decision("immutable", "deny", "late-client")
    row = store.approval_row("immutable")
    assert (row["decision"], row["decided_by"]) == ("allow", "device")
    store.close()


def test_event_cursor_survives_when_all_notifications_are_pruned(tmp_path):
    c = boot(tmp_path)
    response = c.post("/buddy/event", json={"kind": "done", "text": "before"}, headers=H)
    assert response.status_code == 200
    first = c.get("/buddy/events", headers=H).json()["last"]
    # Retention can erase every notification, but the next event ID must still increase.
    store = BuddyStore(tmp_path / "buddy.sqlite3")
    store.db.execute("DELETE FROM notifications")
    store.close()
    restarted = boot(tmp_path)
    after = restarted.post("/buddy/event", json={"kind": "done", "text": "after"}, headers=H).json()
    assert after["id"] > first
    assert [e["text"] for e in restarted.get(f"/buddy/events?since={first}", headers=H).json()["events"]] == ["after"]


def test_event_cursor_counts_phone_text_across_restart(tmp_path):
    # A text_input event must advance the persisted cursor, or the first event
    # after a Core restart reuses its id and a device polling since=that id misses it.
    import uuid
    c = boot(tmp_path)
    c.post("/buddy/event", json={"kind": "done", "text": "before"}, headers=H)
    c.post("/buddy/text", json={"id": str(uuid.uuid4()), "text": "typed", "mode": "insert"}, headers=H)
    last = c.get("/buddy/events", headers=H).json()["last"]
    restarted = boot(tmp_path)
    after = restarted.post("/buddy/event", json={"kind": "done", "text": "after"}, headers=H).json()
    assert after["id"] > last
    assert [e["text"] for e in restarted.get(f"/buddy/events?since={last}", headers=H).json()["events"]] == ["after"]


def test_approved_idempotency_key_rejects_changed_action_even_after_restart(tmp_path):
    c = boot(tmp_path)
    payload = {'tool': 'Send', 'summary': 'Original', 'idempotency_key': 'bound'}
    item = c.post('/buddy/request', json=payload, headers=H).json()
    assert c.post('/buddy/decide', json={'id': item['id'], 'decision': 'allow'}, headers=H).json()['decision'] == 'allow'
    for client, expected in ((c, 'allow'), (boot(tmp_path), 'cancel')):
        assert client.post('/buddy/request', json=payload, headers=H).json()['decision'] == expected
        changed = client.post('/buddy/request', json={**payload, 'summary': 'Different'}, headers=H)
        assert changed.status_code == 409
        assert 'idempotency' in changed.text.lower()
        assert client.post('/buddy/request', json={**payload, 'nod': True}, headers=H).status_code == 409


def _alerts(c):
    return [a for a in c.get("/buddy/alerts", headers=H).json()["alerts"] if a["fired"]]


def test_pending_reminder_survives_restart_and_fires_once(tmp_path):
    c1 = boot(tmp_path)
    c1.post("/buddy/alert", headers=H, json={"title": "later", "kind": "reminder", "delay_seconds": 4})
    c1.post("/buddy/alert", headers=H, json={"title": "now", "kind": "reminder"})
    assert [a["title"] for a in _alerts(c1)] == ["now"]
    c2 = boot(tmp_path)   # restart before 'later' is due
    assert [a["title"] for a in _alerts(c2)] == ["now"]   # fired history is not re-notified
    time.sleep(4.5)
    assert sorted(a["title"] for a in _alerts(c2)) == ["later", "now"]
    def later_notifications():   # durable history: one row per time it actually fired
        db = sqlite3.connect(tmp_path / "buddy.sqlite3")
        return db.execute("SELECT COUNT(*) FROM notifications WHERE data LIKE '%\"later\"%'").fetchone()[0]
    assert later_notifications() == 1
    c3 = boot(tmp_path)   # restart after it fired: no replay
    assert sorted(a["title"] for a in _alerts(c3)) == ["later", "now"]
    assert later_notifications() == 1


def test_reminder_missed_during_downtime_fires_late_not_lost(tmp_path):
    boot(tmp_path).post("/buddy/alert", headers=H, json={"title": "missed", "kind": "reminder", "delay_seconds": 1})
    time.sleep(1.2)   # Core is down while it comes due
    assert [a["title"] for a in _alerts(boot(tmp_path))] == ["missed"]


def test_pending_reminder_survives_a_flood_of_newer_alerts(tmp_path):
    c1 = boot(tmp_path)
    c1.post("/buddy/alert", headers=H, json={"title": "far", "kind": "reminder", "delay_seconds": 3600})
    for i in range(buddy_mod.MAX_ALERTS + 5):
        c1.post("/buddy/alert", headers=H, json={"title": f"info{i}", "kind": "info"})
    c2 = boot(tmp_path)
    assert "far" in [a["title"] for a in c2.get("/buddy/alerts", headers=H).json()["alerts"]]


def test_snoozed_alert_stays_snoozed_across_restart(tmp_path):
    c1 = boot(tmp_path)
    aid = c1.post("/buddy/alert", headers=H, json={"title": "nap", "kind": "reminder", "actions": ["ack", "snooze"]}).json()["id"]
    assert c1.post(f"/buddy/alert/{aid}/snooze", headers=H, json={"minutes": 60}).status_code == 200
    rec = next(a for a in boot(tmp_path).get("/buddy/alerts", headers=H).json()["alerts"] if a["id"] == aid)
    assert rec["outcome"] == "snoozed" and rec["snooze_until"] > time.time() + 3000


def test_expired_snooze_does_not_renag_on_every_restart(tmp_path, monkeypatch):
    c1 = boot(tmp_path)
    aid = c1.post("/buddy/alert", headers=H, json={"title": "nap", "kind": "reminder", "actions": ["ack", "snooze"]}).json()["id"]
    assert c1.post(f"/buddy/alert/{aid}/snooze", headers=H, json={"minutes": 1}).status_code == 200
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 120)   # the snooze has run out
    c1.get("/buddy/alerts", headers=H)                         # poll: the alert re-fires once
    import sqlite3
    row = sqlite3.connect(str(tmp_path / "buddy.sqlite3")).execute("SELECT outcome FROM alerts WHERE id=?", (aid,)).fetchone()
    assert row[0] is None   # the persisted row no longer says "snoozed", so a restart cannot re-nag


def test_approved_idempotent_request_expires_in_memory_and_after_restart(tmp_path, monkeypatch):
    c = boot(tmp_path)
    payload = {"tool": "Send", "summary": "Original", "idempotency_key": "expiry"}
    item = c.post("/buddy/request", json=payload, headers=H).json()
    assert c.post("/buddy/decide", json={"id": item["id"], "decision": "allow"}, headers=H).json()["decision"] == "allow"
    real = buddy_mod.time.monotonic
    monkeypatch.setattr(buddy_mod.time, "monotonic", lambda: real() + buddy_mod.TIMEOUT_S + 1)
    assert c.post("/buddy/request", json=payload, headers=H).json()["decision"] == "cancel"
    restarted = boot(tmp_path)
    assert restarted.get("/buddy/state", params={"id": item["id"]}, headers=H).json()["decision"] == "cancel"
    assert restarted.post("/buddy/request", json=payload, headers=H).json()["decision"] == "cancel"


@pytest.mark.parametrize("removal", ["restart", "eviction"])
def test_stored_allow_cannot_be_used_without_live_deadline(tmp_path, monkeypatch, removal):
    monkeypatch.setattr(buddy_mod, "MAX_ITEMS", 1)
    c = boot(tmp_path)
    payload = {"tool": "Send", "summary": "Original", "idempotency_key": "stored"}
    item = c.post("/buddy/request", json=payload, headers=H).json()
    assert c.post("/buddy/decide", json={"id": item["id"], "decision": "allow"}, headers=H).json()["decision"] == "allow"
    if removal == "restart":
        c = boot(tmp_path)
    else:
        c.post("/buddy/request", json={"tool": "Another"}, headers=H)
    assert c.get("/buddy/state", params={"id": item["id"]}, headers=H).json()["decision"] == "cancel"
    assert c.post("/buddy/request", json=payload, headers=H).json()["decision"] == "cancel"
