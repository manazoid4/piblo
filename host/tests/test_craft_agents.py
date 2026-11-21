"""PibloCraft Mission control: /craft/agents (cswap faked) and task proof round-trip incl. old-DB migration."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from mazhost import craft_agents as ca
from mazhost.app import create_app
from mazhost.buddy_store import MIGRATIONS
from mazhost.config import Settings

real_run = subprocess.run
TOKEN = "test-token-that-is-not-default"
H = {"Authorization": f"Bearer {TOKEN}"}
FUTURE = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
PAST = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
CSWAP = {"accounts": [
    {"number": 1, "email": "secret@example.com", "org": "SecretOrg", "active": True, "usageStatus": "ok",
     "usage": {"fiveHour": {"pct": 10, "resetsAt": FUTURE}, "sevenDay": {"pct": 89, "resetsAt": FUTURE}}},
    {"number": 2, "email": "other@example.com", "active": False, "usageStatus": "ok",
     "usage": {"fiveHour": {"pct": 99, "resetsAt": PAST}, "sevenDay": {"pct": 20, "resetsAt": FUTURE}}},
]}


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    ca._cache.update(at=0.0, value=None)
    monkeypatch.setattr(ca.shutil, "which", lambda name: "C:/bin/codex.exe" if name == "codex" else None)


def client(tmp_path):
    return TestClient(create_app(Settings(token=TOKEN, buddy_db=str(tmp_path / "b.sqlite3"), _env_file=None)))


def test_agents_from_cswap_no_email(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kw):
        if argv[0] != "cswap":
            return real_run(argv, **kw)
        calls.append((argv, kw.get("timeout"), kw.get("shell")))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(CSWAP), stderr="")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)
    c = client(tmp_path)
    assert c.get("/craft/agents").status_code == 401
    r = c.get("/craft/agents", headers=H)
    body = r.json()
    assert body["ok"] is True and "error" not in body
    agents = {a["id"]: a for a in body["agents"]}
    assert list(agents) == ["claude-1", "claude-2", "codex"]
    assert (agents["claude-1"]["pct_left"], agents["claude-1"]["active"], agents["claude-1"]["resets_at"]) == (11, True, FUTURE)
    assert agents["claude-2"]["pct_left"] == 80          # expired 5h window counts as unused
    assert agents["codex"] == {"id": "codex", "label": "Codex", "kind": "codex", "installed": True,
                               "active": False, "pct_left": None, "resets_at": None}
    assert "example.com" not in r.text and "SecretOrg" not in r.text
    c.get("/craft/agents", headers=H)
    assert len(calls) == 1 and calls[0][0] == ["cswap", "list", "--json"] and calls[0][1] == 8   # cached, argv list


@pytest.mark.parametrize("fail", ["missing", "timeout", "badjson"])
def test_cswap_failure_is_still_200(monkeypatch, tmp_path, fail):
    def fake_run(argv, **kw):
        if argv[0] != "cswap":
            return real_run(argv, **kw)
        if fail == "missing":
            raise FileNotFoundError("cswap")
        if fail == "timeout":
            raise subprocess.TimeoutExpired(argv, 8)
        return subprocess.CompletedProcess(argv, 0, stdout="not json", stderr="")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)
    r = client(tmp_path).get("/craft/agents", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == "cswap_unavailable" and [a["id"] for a in body["agents"]] == ["codex"]


def test_task_proof_round_trip_and_old_db_migration(tmp_path):
    db = sqlite3.connect(tmp_path / "b.sqlite3")             # a DB from before the proof column
    db.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for v in (1, 2):
        for stmt in MIGRATIONS[v - 1].split(";"):
            if stmt.strip():
                db.execute(stmt)
        db.execute("INSERT INTO schema_version VALUES (?)", (v,))
    db.execute("INSERT INTO tasks(id, title, status, created, updated) VALUES ('old','Old','done',1,1)")
    db.commit()
    db.close()
    c = client(tmp_path)
    assert {t["id"]: t["proof"] for t in c.get("/buddy/tasks", headers=H).json()["tasks"]} == {"old": ""}
    t = {"id": "t1", "title": "Ship", "status": "running", "proof": "https://x.test/shot.png"}
    c.post("/buddy/task", json=t, headers=H)
    c.post("/buddy/task", json={**t, "status": "done", "proof": ""}, headers=H)     # later update without proof keeps it
    rows = {x["id"]: x for x in c.get("/buddy/tasks", headers=H).json()["tasks"]}
    assert rows["t1"]["proof"] == "https://x.test/shot.png" and rows["t1"]["status"] == "done"
    assert c.post("/buddy/task", json={**t, "proof": "x" * 301}, headers=H).status_code == 422
    assert c.post("/buddy/task", json={"id": "t2", "title": "N", "status": "done"}, headers=H).json()["task"]["proof"] == ""
