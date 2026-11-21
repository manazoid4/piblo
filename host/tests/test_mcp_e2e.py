"""End-to-end: real Core + real piblo-mcp (over stdio) + a headless simulator driving the same
/buddy/* routes as the browser sim and the firmware.

Covers Maz's success test in one file:
  notify -> simulator sees it -> request approval -> simulator approves -> MCP client gets "approved"
  restart Core -> history kept, an old pending approval is NOT pending (expired)
  duplicate idempotency key -> single record
  no answer -> expired (via restart, which is how Core closes a pending approval)
  corrupt DB -> Core recovers and starts fresh

Skipped (with a clear reason) when node or npm are unavailable, since the MCP server is TypeScript.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from mazhost.app import create_app
from mazhost.config import Settings

REPO = Path(__file__).resolve().parents[2]
MCP_DIR = REPO / "host" / "mcp"
BRIDGE = MCP_DIR / "test" / "e2e_bridge.mjs"
DIST = MCP_DIR / "dist" / "index.js"


@pytest.fixture(autouse=True)
def legacy_unsigned_device(monkeypatch):
    # These existing tests exercise the current unsigned simulator/firmware path.
    monkeypatch.setenv("MAZ_ASK_LEGACY_DEVICE_DECIDE", "1")
    # These tests exercise the bridge, not model startup or project discovery.
    for warmup in ("SpeechToText.warm", "SpeechOut.warm", "Models.warm", "MazCore._kick_refresh"):
        monkeypatch.setattr(f"mazhost.app.{warmup}", lambda *_: None)

TOKEN = "e2e-token-not-default"
H = {"Authorization": f"Bearer {TOKEN}", "X-Piblo-Client": "simulator/1"}

NODE = shutil.which("node")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_mcp() -> None:
    """Build dist/index.js once. CI may pre-build it; here we build on demand so `pytest` is self-contained."""
    if DIST.exists():
        return
    # Windows Node distributions expose npm.cmd; subprocess without a shell cannot execute
    # the bare "npm" shim on the GitHub Windows runner.
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        pytest.skip("npm unavailable, cannot build piblo-mcp")
    subprocess.run([npm, "ci"], cwd=MCP_DIR, check=True, capture_output=True, timeout=300)
    subprocess.run([npm, "run", "build"], cwd=MCP_DIR, check=True, capture_output=True, timeout=300)


def mcp_call(url: str, tool: str, args: dict, timeout: int = 90) -> dict:
    """One tool call through the real MCP server; returns the parsed tool result."""
    assert NODE, "node required"
    proc = subprocess.run(
        [NODE, str(BRIDGE), url, TOKEN, tool, json.dumps(args)],
        capture_output=True, text=True, timeout=timeout,
    )
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    payload = json.loads(out["text"])
    payload["_isError"] = out["isError"]
    return payload


class Sim:
    """Headless simulator: the same routes the browser /sim page drives (poll events, decide, ack)."""

    def __init__(self, base: str) -> None:
        self.c = httpx.Client(base_url=base, headers=H, timeout=10)
        self.since = 0

    def events(self) -> list[dict]:
        page = self.c.get("/buddy/events", params={"since": self.since}).json()
        for ev in page.get("events", []):
            self.since = ev["id"]
        return page.get("events", [])

    def pending(self) -> list[dict]:
        return self.c.get("/buddy/summary").json().get("items", [])

    def decide(self, rid: str, decision: str) -> None:
        self.c.post("/buddy/decide", json={"id": rid, "decision": decision})

    def ack(self, aid: str) -> None:
        self.c.post(f"/buddy/alert/{aid}/ack")

    def notify_me(self, title: str, key: str = "") -> dict:
        r = self.c.post("/buddy/alert", json={
            "source": "sim", "kind": "info", "title": title, "priority": "normal",
            "actions": ["ack"], "idempotency_key": key}).json()
        return r


def _wait_until(pred, timeout=15.0, interval=0.2):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = pred()
        if last:
            return last
        time.sleep(interval)
    return last


class CoreHandle:
    """Core running on a real port (needed because the node MCP process and the sim both use HTTP)."""

    def __init__(self, data_dir: Path, mcp_port: int = 0, private_mode: bool = False):
        self.port = _free_port()
        self.data_dir = data_dir
        cfg = Settings(
            token=TOKEN, port=self.port, mcp_port=mcp_port, repo_dir=str(REPO),
            data_dir=str(data_dir), buddy_db=str(data_dir / "buddy.sqlite3"),
            work_dir=str(data_dir / "work"), discovery_port=0, remote_detect=False,
            autoupdate=False, private_mode=private_mode, _env_file=None,
        )
        self.app = create_app(cfg)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error"))
        self._thread = threading.Thread(target=self.server.run, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        # wait for readiness
        with httpx.Client(base_url=self.base, headers=H, timeout=5) as c:
            assert _wait_until(lambda: _ping(c), timeout=15.0), "Core did not start"

    def stop(self) -> None:
        """Actually terminate the HTTP server before re-opening the same persistent DB."""
        if not self._thread.is_alive():
            return
        self.server.should_exit = True
        self._thread.join(timeout=15)
        assert not self._thread.is_alive(), "Core failed to stop"


def _ping(c) -> bool:
    try:
        return c.get("/health").status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture
def core(tmp_path):
    if not NODE:
        pytest.skip("node unavailable, skipping piblo-mcp e2e")
    _build_mcp()
    data_dir = tmp_path / "piblo-e2e"
    data_dir.mkdir()
    handle = CoreHandle(data_dir)
    try:
        yield handle, data_dir
    finally:
        handle.stop()


# ---------------------------------------------------------------------------


def test_core_manages_authenticated_http_mcp(tmp_path, monkeypatch):
    if not NODE:
        pytest.skip("node unavailable, skipping piblo-mcp e2e")
    _build_mcp()
    mcp_port = _free_port()
    monkeypatch.setenv("PIBLO_DISPLAY_URL", "http://127.0.0.1:9")
    handle = CoreHandle(tmp_path, mcp_port=mcp_port, private_mode=True)
    url = f"http://127.0.0.1:{mcp_port}/mcp"
    try:
        def rejected_without_token():
            try:
                return httpx.post(url, timeout=1, trust_env=False).status_code == 401
            except (httpx.ConnectError, httpx.ConnectTimeout):
                return False

        assert _wait_until(rejected_without_token), "Core did not start the authenticated MCP listener"
        assert httpx.post(url, headers={"Authorization": "Bearer wrong"}, timeout=2).status_code == 401
        result = mcp_call(url, "piblo_status", {}, timeout=10)
        assert result["ok"] is True and result["state"] == "disconnected"
        devices = mcp_call(url, "piblo_devices", {}, timeout=10)
        assert devices["devices"][1]["state"] == "unknown"
        assert httpx.get(handle.base + "/health", timeout=2).status_code == 200
    finally:
        handle.stop()

    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", mcp_port), timeout=2):
            pass


def test_success_flow_restart_and_history(core):
    handle, data_dir = core
    base = handle.base
    sim = Sim(base)

    # 1. MCP notifies -> the simulator sees it.
    n = mcp_call(base, "piblo_notify", {"title": "Deploy finished", "body": "all green", "idempotency_key": "n1"})
    assert n["ok"] is True and n["duplicate"] is False
    seen = _wait_until(lambda: sim.events() or None)
    assert seen and any("Deploy finished" in e.get("text", "") for e in seen)

    # 2. MCP asks for approval (blocking); the simulator approves; the client gets "approved".
    result = {}
    def ask():
        result.update(mcp_call(base, "piblo_request_approval",
                               {"action": "Deploy to prod", "summary": "push the release", "sensitive": True}))
    t = threading.Thread(target=ask, daemon=True)
    t.start()
    pending = _wait_until(lambda: sim.pending() or None, timeout=20.0)
    assert pending, "the approval never reached the device"
    sim.decide(pending[0]["id"], "allow")
    t.join(timeout=60)
    assert result.get("decision") == "approved", result

    # history holds both, no secrets in the export
    hist = httpx.get(base + "/buddy/history", headers=H).json()
    assert any("Deploy to prod" in a["tool"] for a in hist["approvals"])

    # 2b. Phone keyboard (the Android wire calls): one insert, a duplicate send, the device ack.
    phone = {"Authorization": f"Bearer {TOKEN}", "X-Piblo-Client": "android/1"}
    tid = "0b9f3c1e-5d7a-4c2b-9e8f-1a2b3c4d5e6f"
    body = {"id": tid, "text": "milk, eggs", "mode": "insert"}
    assert httpx.post(base + "/buddy/text", json=body, headers=phone).json()["status"] == "pending"
    assert httpx.post(base + "/buddy/text", json=body, headers=phone).json()["status"] == "pending"  # retry, same id
    texts = [e for e in _wait_until(lambda: sim.events() or None) if e["kind"] == "text_input"]
    assert [e["text_input"]["id"] for e in texts] == [tid], "duplicate send must not create a second insert"
    assert httpx.post(base + f"/buddy/text/{tid}/ack", json={"status": "inserted"}, headers=H).status_code == 200
    assert httpx.get(base + f"/buddy/text/{tid}", headers=phone).json()["status"] == "inserted"
    # phone text never became an approval, and a bad token gets nothing
    assert all(p.get("tool") != "milk, eggs" for p in sim.pending())
    assert httpx.post(base + "/buddy/text", json={**body, "id": "1b9f3c1e-5d7a-4c2b-9e8f-1a2b3c4d5e6f"},
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    cursor = sim.since

    # 3. leave an approval unanswered, restart, and prove it is NOT pending (it expired) while history survives.
    unanswered = httpx.post(base + "/buddy/request", json={"tool": "Later", "summary": "never answered", "agent": "x"},
                            headers=H).json()
    httpx.post(base + "/buddy/demo", headers=H)  # second approval, also pending, for good measure
    handle.stop()

    # New Core instance reads the same WAL-backed SQLite file after the old HTTP server stopped.
    restarted = CoreHandle(data_dir)
    try:
        with httpx.Client(base_url=restarted.base, headers=H, timeout=10) as c2:
            hist2 = c2.get("/buddy/history").json()
            assert any("Deploy to prod" in a["tool"] for a in hist2["approvals"])
            # no pending approval survived the restart (they all expired on open)
            summary = c2.get("/buddy/summary").json()
            assert summary["items"] == [], summary
            state = c2.get("/buddy/state", params={"id": unanswered["id"]}).json()
            assert state["decision"] == "cancel" and state.get("expired") is True
            # event numbering continues: a device polling from its old cursor sees the next event
            c2.post("/buddy/event", json={"kind": "done", "text": "after restart"})
            evs = c2.get("/buddy/events", params={"since": cursor}).json()["events"]
            assert [e["text"] for e in evs] == ["after restart"] and evs[0]["id"] > cursor
            # phone text is ephemeral: gone after restart, never re-delivered
            assert c2.get(f"/buddy/text/{tid}").status_code == 404
    finally:
        restarted.stop()


def test_duplicate_idempotency_key_single_record(core):
    handle, _ = core
    sim = Sim(handle.base)
    first = mcp_call(handle.base, "piblo_notify", {"title": "Once", "idempotency_key": "dup-1"})
    again = mcp_call(handle.base, "piblo_notify", {"title": "Once", "idempotency_key": "dup-1"})
    assert first["ok"] is True and again.get("duplicate") is True and again["id"] == first["id"]


def test_no_answer_expires_at_client(core):
    handle, _ = core
    r = mcp_call(handle.base, "piblo_request_approval", {"action": "Never answered", "wait_seconds": 5}, timeout=30)
    assert r["decision"] == "expired", r


def test_corrupt_db_recovers(tmp_path):
    bad = tmp_path / "corrupt.sqlite3"
    bad.write_bytes(b"this is not a sqlite database at all")
    app = create_app(Settings(token=TOKEN, buddy_db=str(bad), _env_file=None))
    # create_app builds the store at startup; a corrupt file must be quarantined, not crash.
    c = TestClient(app)
    assert c.get("/buddy/history", headers=H).status_code == 200
    assert any(p.name.startswith("corrupt.sqlite3.corrupt-") for p in tmp_path.iterdir())
