"""Piblo device -> Core -> PibloCraft: nothing is sent without a yes on the device."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from mazhost import piblocraft_bridge
from mazhost.app import create_app
from mazhost.config import Settings

H = {"Authorization": "Bearer bridge-test-token-not-default", "X-Piblo-Client": "simulator/1"}


class Reply:
    status_code = 200
    def raise_for_status(self): pass


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("MAZ_ASK_LEGACY_DEVICE_DECIDE", "1")  # the unsigned simulator stands in for the device
    sent = []
    cfg = Settings(token="bridge-test-token-not-default", _env_file=None, data_dir=str(tmp_path / "data"),
                   piblocraft_bridge_url="http://receiver.invalid/job", buddy_db=str(tmp_path / "b.sqlite3"))
    monkeypatch.setattr(piblocraft_bridge.JobBridge, "_post", staticmethod(lambda u, j: sent.append((u, j)) or Reply()))
    app = create_app(cfg)
    return TestClient(app, headers=H), sent, tmp_path / "data" / "bridge.log"


def _wait(c, jid, want, secs=8):
    end = time.time() + secs
    while time.time() < end:
        s = c.get(f"/bridge/job/{jid}").json()["status"]
        if s in want:
            return s
        time.sleep(0.05)
    return s


def test_sent_only_after_device_allows(rig):
    c, sent, log = rig
    job = c.post("/bridge/job", json={"command": "pc run tests", "cwd": "C:/work"}).json()
    time.sleep(0.3)
    assert sent == [] and c.get(f"/bridge/job/{job['id']}").json()["status"] == "waiting"  # no yes yet: nothing sent
    pending = c.get("/buddy/pending").json()["pending"]
    assert pending[0]["id"] == job["approval_id"] and "pc run tests" in json.dumps(pending[0])
    c.post("/buddy/decide", json={"id": job["approval_id"], "decision": "allow"})
    assert _wait(c, job["id"], {"sent", "send_failed"}) == "sent"
    assert sent == [("http://receiver.invalid/job", {"id": job["id"], "command": "pc run tests", "cwd": "C:/work",
                                                      "requested_by": "piblo-device", "approval_required": True})]
    events = [json.loads(l)["event"] for l in log.read_text().splitlines()]
    assert events == ["requested", "sent"]  # command is on record on the Core side


def test_denied_is_never_sent(rig):
    c, sent, log = rig
    job = c.post("/bridge/job", json={"command": "rm -rf build"}).json()
    c.post("/buddy/decide", json={"id": job["approval_id"], "decision": "deny"})
    assert _wait(c, job["id"], {"denied"}) == "denied"
    assert sent == [] and json.loads(log.read_text().splitlines()[-1])["event"] == "not_sent"


def test_rejects_multiline_and_unknown(rig):
    c, _, _ = rig
    assert c.post("/bridge/job", json={"command": "a\nb"}).status_code == 422
    assert c.get("/bridge/job/nope").status_code == 404
