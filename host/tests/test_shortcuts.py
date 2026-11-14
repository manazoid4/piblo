from __future__ import annotations

import httpx
import pytest

from mazhost.shortcuts import Shortcuts


class Buddy:
    decision = None

    def request(self, tool, summary, **kwargs):
        self.summary = summary
        return {"id": "one"}

    def state(self, request_id):
        return {"known": True, "decision": self.decision}


def test_approval_binds_url_and_runs_once():
    buddy = Buddy()
    sent = []
    transport = httpx.MockTransport(lambda request: (sent.append(request.url) or httpx.Response(200, text="healthy")))
    actions = {"check server health": "https://example.com/health"}
    shortcuts = Shortcuts(actions, buddy, transport=transport)
    rid = shortcuts.request("check server health")["id"]
    actions["check server health"] = "https://evil.example/"
    assert "POST https://example.com/health" in buddy.summary
    assert shortcuts.poll(rid)["status"] == "pending"
    buddy.decision = "allow"
    assert shortcuts.poll(rid)["output"] == "healthy"
    shortcuts.poll(rid)
    assert [str(url) for url in sent] == ["https://example.com/health"]


def test_deny_unknown_and_invalid_urls():
    buddy = Buddy()
    shortcuts = Shortcuts({"check": "https://example.com/health"}, buddy)
    rid = shortcuts.request("check")["id"]
    buddy.decision = "deny"
    assert shortcuts.poll(rid)["status"] == "denied"
    with pytest.raises(ValueError):
        shortcuts.request("anything else")
    for url in ("file:///tmp/x", "https://u:p@example.com", "https://example.com/?token=x"):
        with pytest.raises(ValueError):
            Shortcuts({"check": url}, buddy)


def test_command_requires_grant_and_uses_configured_snapshot():
    class Executor:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return {"ok": True, "stdout": "ready"}

    buddy, executor = Buddy(), Executor()
    target = {"command": "Get-Service", "cwd": "C:/work"}
    shortcuts = Shortcuts({"check": target}, buddy, executor=executor)
    rid = shortcuts.request("check")["id"]
    target["command"] = "Remove-Item *"
    buddy.decision = "allow"
    assert shortcuts.poll(rid)["status"] == "grant_required"
    assert shortcuts.poll(rid, grant_token="signed")["result"]["stdout"] == "ready"
    shortcuts.poll(rid, grant_token="signed")
    assert executor.calls == [{"grant_token": "signed", "command": "Get-Service", "cwd": "C:/work", "shell": "powershell"}]
