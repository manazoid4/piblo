from __future__ import annotations

import pytest

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from mazhost import buddy as buddy_mod
from mazhost.app import create_app
from mazhost.config import Settings


@pytest.fixture(autouse=True)
def legacy_unsigned_device(monkeypatch):
    # These existing tests exercise the current unsigned simulator/firmware path.
    monkeypatch.setenv("MAZ_ASK_LEGACY_DEVICE_DECIDE", "1")

TOKEN = "test-token-that-is-not-default"
H = {"Authorization": f"Bearer {TOKEN}"}
HOOK = Path(__file__).parents[1] / "hooks" / "nod_permission_hook.py"
EVENT = {"session_id": "s1", "hook_event_name": "PermissionRequest",
         "tool_name": "Bash", "tool_input": {"command": "npm test"}}


def api():
    return TestClient(create_app(Settings(token=TOKEN, _env_file=None)))


def test_request_pending_decide():
    c = api()
    rid = c.post("/buddy/request", json={"tool": "Bash", "summary": "ls"}, headers=H).json()["id"]
    assert [p["id"] for p in c.get("/buddy/pending", headers=H).json()["pending"]] == [rid]
    assert c.get(f"/buddy/state?id={rid}", headers=H).json()["decision"] is None
    c.post("/buddy/decide", json={"id": rid, "decision": "deny"}, headers=H)
    assert c.get(f"/buddy/state?id={rid}", headers=H).json()["decision"] == "deny"
    assert c.get("/buddy/pending", headers=H).json()["pending"] == []


def test_allow_all_is_rejected_without_granting_other_requests():
    c = api()
    r1 = c.post("/buddy/request", json={"tool": "Bash", "session_id": "s"}, headers=H).json()["id"]
    r2 = c.post("/buddy/request", json={"tool": "Edit", "session_id": "s"}, headers=H).json()["id"]
    assert c.post("/buddy/decide", json={"id": r1, "decision": "allow_all"}, headers=H).status_code == 400
    assert c.get(f"/buddy/state?id={r1}", headers=H).json()["decision"] is None
    assert c.get(f"/buddy/state?id={r2}", headers=H).json()["decision"] is None
    assert c.post("/buddy/allow-all", json={"session_id": "s", "on": True}, headers=H).status_code == 400
    assert c.post("/buddy/request", json={"tool": "Bash", "session_id": "s"}, headers=H).json()["decision"] is None


def test_decisions_are_bound_to_one_request():
    c = api()
    r1 = c.post("/buddy/request", json={"tool": "Bash", "session_id": "s"}, headers=H).json()["id"]
    r2 = c.post("/buddy/request", json={"tool": "Bash", "session_id": "s"}, headers=H).json()["id"]
    assert c.post("/buddy/decide", json={"id": r1, "decision": "allow"}, headers=H).json()["decision"] == "allow"
    assert c.get(f"/buddy/state?id={r2}", headers=H).json()["decision"] is None
    assert c.post("/buddy/decide", json={"id": r1, "decision": "deny"}, headers=H).json()["decision"] == "allow"


def test_expired_requests_cannot_be_approved(monkeypatch):
    c = api()
    rid = c.post("/buddy/request", json={"tool": "Bash"}, headers=H).json()["id"]
    import mazhost.buddy as bm
    real = bm.time.monotonic
    monkeypatch.setattr(bm.time, "monotonic", lambda: real() + bm.TIMEOUT_S + 10)
    assert c.get("/buddy/summary", headers=H).json()["count"] == 0
    assert c.get("/buddy/pending", headers=H).json()["pending"] == []
    response = c.post("/buddy/decide", json={"id": rid, "decision": "allow"}, headers=H)
    assert response.status_code == 409
    assert response.json()["detail"] == "approval_expired"
    assert c.get(f"/buddy/state?id={rid}", headers=H).json()["decision"] == "cancel"


def test_approved_request_cannot_be_consumed_after_deadline(monkeypatch):
    c = api()
    rid = c.post("/buddy/request", json={"tool": "Bash"}, headers=H).json()["id"]
    c.post("/buddy/decide", json={"id": rid, "decision": "allow"}, headers=H)
    import mazhost.buddy as bm
    real = bm.time.monotonic
    monkeypatch.setattr(bm.time, "monotonic", lambda: real() + bm.TIMEOUT_S + 10)
    state = c.get(f"/buddy/state?id={rid}", headers=H).json()
    assert state["decision"] == "cancel" and state["expired"] is True
    assert c.post("/buddy/decide", json={"id": rid, "decision": "allow"}, headers=H).status_code == 409
    assert c.post("/buddy/decide", json={"id": rid, "decision": "allow_all"}, headers=H).status_code == 400

def test_auth_required():
    assert api().get("/buddy/pending").status_code == 401


def load_hook(monkeypatch, client, timeout):
    monkeypatch.setenv("MAZ_TOKEN", TOKEN)
    monkeypatch.setenv("MAZ_BUDDY_TIMEOUT", timeout)
    spec = importlib.util.spec_from_file_location("hook", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    hook.call = lambda m, p, body=None, timeout=10: client.request(m, p, json=body, headers=H).json()
    return hook


def run_main(hook, monkeypatch, capsys):
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(EVENT)))
    assert hook.main() == 0
    return capsys.readouterr()


def test_acceptance_request_decide_hook_prints_allow(monkeypatch, capsys):
    c = api()
    hook = load_hook(monkeypatch, c, "60")

    def device():
        for _ in range(200):
            p = c.get("/buddy/pending", headers=H).json()["pending"]
            if p:
                assert p[0]["tool"] == "Bash" and "npm test" in p[0]["summary"]
                c.post("/buddy/decide", json={"id": p[0]["id"], "decision": "allow"}, headers=H)
                return
            time.sleep(0.05)

    t = threading.Thread(target=device)
    t.start()
    out = run_main(hook, monkeypatch, capsys)
    t.join()
    got = json.loads(out.out)["hookSpecificOutput"]
    assert got == {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}


def test_acceptance_timeout_prints_ask(monkeypatch, capsys):
    c = api()
    hook = load_hook(monkeypatch, c, "0.3")
    out = run_main(hook, monkeypatch, capsys)
    assert out.out == "" and out.err.strip() == "ask"  # no stdout = terminal prompt
    assert c.get("/buddy/pending", headers=H).json()["pending"] == []  # cancelled


def test_core_down_asks():
    env = {**os.environ, "MAZ_TOKEN": TOKEN, "MAZ_CORE_URL": "http://127.0.0.1:1"}
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(EVENT), text=True,
                         capture_output=True, env=env, timeout=30)
    assert out.returncode == 0 and out.stdout == "" and out.stderr.strip() == "ask"


def test_summary_states_and_fields():
    c = api()
    s = c.get("/buddy/summary", headers=H).json()
    assert s["agent"] == "idle" and s["count"] == 0 and s["items"] == []
    a = c.post("/buddy/request", json={"tool": "Edit", "summary": "a.py", "project": "maz"}, headers=H).json()["id"]
    b = c.post("/buddy/request", json={"tool": "Bash", "summary": "ls"}, headers=H).json()["id"]
    s = c.get("/buddy/summary", headers=H).json()
    assert s["agent"] == "needs_you" and s["count"] == 2
    assert [i["id"] for i in s["items"]] == [a, b]  # oldest first
    assert s["items"][0]["project"] == "maz" and 55 <= s["items"][0]["left"] <= 60
    c.post("/buddy/decide", json={"id": a, "decision": "allow"}, headers=H)
    c.post("/buddy/decide", json={"id": b, "decision": "cancel"}, headers=H)
    s = c.get("/buddy/summary", headers=H).json()
    assert s["agent"] == "working" and s["count"] == 0  # recent activity, nothing pending


def test_summary_requires_auth_and_hides_expired():
    c = api()
    assert c.get("/buddy/summary").status_code == 401
    rid = c.post("/buddy/request", json={"tool": "Bash"}, headers=H).json()["id"]
    import mazhost.buddy as bm
    real = bm.time.monotonic
    bm.time.monotonic = lambda: real() + 100
    try:
        assert c.get("/buddy/summary", headers=H).json()["count"] == 0
        assert c.get("/buddy/pending", headers=H).json()["pending"] == []
    finally:
        bm.time.monotonic = real
    assert rid


def test_hook_sends_project(monkeypatch, capsys):
    c = api()
    hook = load_hook(monkeypatch, c, "0.2")
    import io
    ev = {**EVENT, "cwd": "C:\\Users\\x\\maz-pocket"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(ev)))
    sent = []
    orig = hook.call
    hook.call = lambda m, p, body=None, timeout=10: (sent.append(body), orig(m, p, body, timeout))[1]
    hook.main()
    assert sent[0]["project"] == "maz-pocket"


# ---- agent alerts (/buddy/event) ----
STOP_HOOK = Path(__file__).parents[1] / "hooks" / "nod_stop_hook.py"


def test_event_post_poll_since():
    c = api()
    assert c.get("/buddy/events", headers=H).json() == {"ok": True, "events": [], "last": 0}
    a = c.post("/buddy/event", json={"kind": "done", "text": "hi", "source": "t"}, headers=H).json()
    b = c.post("/buddy/event", json={"kind": "usage", "text": "90%"}, headers=H).json()
    assert (a["id"], b["id"]) == (1, 2)
    got = c.get("/buddy/events?since=1", headers=H).json()
    assert got["last"] == 2 and [e["id"] for e in got["events"]] == [2]
    assert set(got["events"][0]) == {"id", "kind", "text", "source", "ts"}


def test_event_ring_cap_bad_kind_truncate_auth():
    c = api()
    for i in range(25):
        c.post("/buddy/event", json={"kind": "needs", "text": str(i)}, headers=H)
    ev = c.get("/buddy/events", headers=H).json()
    assert len(ev["events"]) == 20 and ev["events"][0]["id"] == 6 and ev["last"] == 25
    assert c.post("/buddy/event", json={"kind": "nope", "text": "x"}, headers=H).status_code == 422
    long = c.post("/buddy/event", json={"kind": "error", "text": "x" * 500}, headers=H).json()
    assert len(long["text"]) == 160
    assert c.post("/buddy/event", json={"kind": "done"}).status_code == 401
    assert c.get("/buddy/events").status_code == 401


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wire_notify(monkeypatch, c):
    monkeypatch.setenv("NOD_TOKEN", TOKEN)
    notify = load(NOTIFY, "nod_notify")
    sys.modules["nod_notify"] = notify  # the stop hook imports it by name
    notify.call = lambda p, body, timeout=3: c.post(p, json=body, headers=H).json()
    return notify


def stdin(monkeypatch, text):
    import io
    f = io.StringIO(text)
    f.isatty = lambda: False
    monkeypatch.setattr(sys, "stdin", f)


def test_stop_hook_posts_and_exits_zero(monkeypatch, capsys):
    c = api()
    wire_notify(monkeypatch, c)
    hook = load(STOP_HOOK, "stop_hook")
    stdin(monkeypatch, json.dumps(
        {"hook_event_name": "Stop", "last_assistant_message": "\nAll done here\nmore"}))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""
    e = c.get("/buddy/events", headers=H).json()["events"][0]
    assert (e["kind"], e["text"], e["source"]) == ("done", "All done here", "claude-code")
    stdin(monkeypatch, "{}")
    hook.main()
    assert c.get("/buddy/events", headers=H).json()["events"][1]["text"] == "Claude finished"


def test_stop_hook_dead_hub_silent_exit_zero():
    env = {**os.environ, "NOD_TOKEN": TOKEN, "NOD_CORE_URL": "http://127.0.0.1:1"}
    out = subprocess.run([sys.executable, str(STOP_HOOK)], input="{}", text=True,
                         capture_output=True, env=env, timeout=30)
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


# ---- generic CLI: nod_notify.py ----
NOTIFY = Path(__file__).parents[1] / "hooks" / "nod_notify.py"


def test_notify_args_path_stores_source(monkeypatch):
    c = api()
    n = wire_notify(monkeypatch, c)
    assert n.main(["done", "build finished", "--source", "codex"]) == 0
    assert n.main(["usage", "90% used", "--source", "x" * 99]) == 0
    evs = c.get("/buddy/events", headers=H).json()["events"]
    assert [(e["kind"], e["text"], e["source"]) for e in evs] == [
        ("done", "build finished", "codex"), ("usage", "90% used", "x" * 40)]


def test_notify_stdin_json_fields(monkeypatch):
    c = api()
    n = wire_notify(monkeypatch, c)
    stdin(monkeypatch, json.dumps({"message": "\n\nfirst line\nsecond " + "z" * 300}))
    assert n.main(["needs", "--source", "gemini"]) == 0
    stdin(monkeypatch, json.dumps({"summary": "s" * 300}))
    n.main(["done"])
    stdin(monkeypatch, "plain text line\nmore")
    n.main(["error"])
    evs = c.get("/buddy/events", headers=H).json()["events"]
    assert (evs[0]["text"], evs[0]["source"]) == ("first line", "gemini")
    assert evs[1]["text"] == "s" * 160 and evs[2]["text"] == "plain text line"


def test_notify_json_as_argument_codex_style(monkeypatch):
    c = api()
    n = wire_notify(monkeypatch, c)
    payload = json.dumps({"type": "agent-turn-complete", "last-assistant-message": "Fixed it"})
    assert n.main(["done", "--source", "codex", payload]) == 0
    e = c.get("/buddy/events", headers=H).json()["events"][0]
    assert (e["text"], e["source"]) == ("Fixed it", "codex")


def test_notify_nod_env_beats_maz_env(monkeypatch):
    n = load(NOTIFY, "nod_notify_env")
    monkeypatch.setenv("MAZ_TOKEN", "old")
    monkeypatch.setenv("MAZ_CORE_URL", "http://old")
    assert (n.env("TOKEN"), n.env("CORE_URL")) == ("old", "http://old")
    monkeypatch.setenv("NOD_TOKEN", "new")
    monkeypatch.setenv("NOD_CORE_URL", "http://new")
    assert (n.env("TOKEN"), n.env("CORE_URL")) == ("new", "http://new")


def test_notify_dead_hub_and_bad_input_silent_exit_zero():
    env = {**os.environ, "NOD_TOKEN": TOKEN, "NOD_CORE_URL": "http://127.0.0.1:1"}
    for argv in (["done", "hi"], ["bogus", "hi"], []):
        out = subprocess.run([sys.executable, str(NOTIFY), *argv], input="", text=True,
                             capture_output=True, env=env, timeout=30)
        assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_permission_hook_reads_nod_env(monkeypatch):
    monkeypatch.delenv("MAZ_TOKEN", raising=False)
    monkeypatch.setenv("NOD_TOKEN", "t")
    assert load(HOOK, "perm").TOKEN == "t"


# ---- security: events are notify-only (integration regression tests) ----
def test_events_cannot_approve_or_decide():
    c = api()
    rid = c.post("/buddy/request", json={"tool": "Bash", "session_id": "s"}, headers=H).json()["id"]
    # an agent (or attacker with the token) spams every event shape, including ids and decisions
    for kind in ("done", "usage", "needs", "error"):
        for text in ("approved", "allow", rid, "decision: allow"):
            c.post("/buddy/event", json={"kind": kind, "text": text, "source": "halt"}, headers=H)
    extra = c.post("/buddy/event", json={"kind": "done", "id": rid, "decision": "allow"}, headers=H)
    assert extra.status_code == 200 and "decision" not in extra.json()
    assert c.post("/buddy/event", json={"kind": "allow", "text": rid}, headers=H).status_code == 422
    assert c.get(f"/buddy/state?id={rid}", headers=H).json()["decision"] is None
    assert [p["id"] for p in c.get("/buddy/pending", headers=H).json()["pending"]] == [rid]
    assert c.get("/buddy/summary", headers=H).json()["count"] == 1


def test_event_endpoints_do_not_touch_approvals_or_grants():
    c = api()
    c.post("/buddy/event", json={"kind": "needs", "text": "x", "source": "s"}, headers=H)
    assert c.get("/buddy/pending", headers=H).json()["pending"] == []
    later = c.post("/buddy/request", json={"tool": "Bash", "session_id": "s"}, headers=H).json()
    assert later["decision"] is None  # an event never pre-approves a later request
    assert c.post("/buddy/event", json={"kind": "done", "text": "t" * 5000}, headers=H).status_code == 422

# Today counters never grant approval and reset at midnight (Core local time).
def test_today_counts_and_permissions(monkeypatch):
    day = {"value": "2026-10-11"}
    monkeypatch.setattr(buddy_mod, "_local_today", lambda: day["value"])
    c = api()
    assert c.get("/buddy/today").status_code == 401
    assert c.get("/buddy/today", headers=H).json()["done"] == 0
    for kind in ("done", "done", "needs", "error", "usage"):
        response = c.post("/buddy/event", json={"kind": kind, "source": "codex", "text": kind}, headers=H)
        assert response.status_code == 200
    r1 = c.post("/buddy/request", json={"tool": "Bash"}, headers=H).json()["id"]
    r2 = c.post("/buddy/request", json={"tool": "Write"}, headers=H).json()["id"]
    assert c.post("/buddy/decide", json={"id": r1, "decision": "allow"}, headers=H).status_code == 200
    assert c.post("/buddy/decide", json={"id": r2, "decision": "deny"}, headers=H).status_code == 200
    got = c.get("/buddy/today", headers=H).json()
    assert tuple(got[k] for k in ("done", "needs", "errors", "usage", "approved", "denied")) == (2, 1, 1, 1, 1, 1)
    assert got["agents"] == [{"source": "codex", "done": 2}]
    assert got["last"]["kind"] == "usage"
    assert c.post("/buddy/decide", json={"id": r1, "decision": "allow_all"}, headers=H).status_code == 400
    assert c.get("/buddy/today", headers=H).json()["approved"] == 1
    day["value"] = "2026-10-12"
    fresh = c.get("/buddy/today", headers=H).json()
    assert fresh["done"] == fresh["approved"] == 0 and fresh["agents"] == [] and fresh["last"] is None

def test_today_agent_cap_and_no_automatic_decision(monkeypatch):
    monkeypatch.setattr(buddy_mod, "_local_today", lambda: "2026-10-11")
    c = api()
    for i in range(10):
        c.post("/buddy/event", json={"kind": "done", "source": f"a{i}"}, headers=H)
    rows = c.get("/buddy/today", headers=H).json()["agents"]
    assert len(rows) == 9 and rows[-1] == {"source": "other", "done": 2}
    rid = c.post("/buddy/request", json={"tool": "Bash"}, headers=H).json()["id"]
    assert c.get(f"/buddy/state?id={rid}", headers=H).json()["decision"] is None


def test_saved_catch_up_keeps_buddy_activity_separate(tmp_path):
    from mazhost import dumps

    inbox = tmp_path / "inbox"
    c = TestClient(create_app(Settings(token=TOKEN, _env_file=None, dumps_dir=str(inbox))))
    dump_id = dumps.save_dump(inbox, "Bring the folder", {"actions": ["Bring the folder"]}, source="web")["id"]
    before = c.get("/buddy/today", headers=H).json()
    assert c.get("/dumps/catch-up", headers=H).json()["actions"][0]["id"] == dump_id
    c.post(f"/dumps/{dump_id}/done", headers=H, json={"agent": "web"})
    assert c.get("/dumps/catch-up", headers=H).json()["actions"] == []
    assert c.get("/buddy/today", headers=H).json() == before

def test_halt_immediately_denies_and_never_auto_allows():
    c = api()
    a = c.post("/buddy/request", json={"tool": "Bash"}, headers=H).json()["id"]
    res = c.post("/buddy/halt", json={"on": True, "minutes": 30}, headers=H)
    assert res.status_code == 200 and res.json()["halted"] is True
    assert c.get(f"/buddy/state?id={a}", headers=H).json()["decision"] == "deny"
    blocked = c.post("/buddy/request", json={"tool": "Write"}, headers=H).json()
    assert blocked["decision"] == "deny" and "Halted" in blocked["reason"]
    assert c.post("/buddy/decide", json={"id": blocked["id"], "decision": "allow"}, headers=H).json()["decision"] == "deny"
    assert c.post("/buddy/decide", json={"id": blocked["id"], "decision": "allow_all"}, headers=H).status_code == 400
    assert c.post("/buddy/allow-all", json={"session_id": "s", "on": True}, headers=H).status_code == 400
    assert c.post("/buddy/halt", json={"on": False}, headers=H).json()["halted"] is False
    newer = c.post("/buddy/request", json={"tool": "Write"}, headers=H).json()
    assert newer["decision"] is None

def test_halt_requires_auth_and_bounded_duration():
    c = api()
    assert c.get("/buddy/halt").status_code == 401
    assert c.post("/buddy/halt", json={"on": True}).status_code == 401
    for minutes in (0, -1, 481):
        assert c.post("/buddy/halt", json={"on": True, "minutes": minutes}, headers=H).status_code == 422
