import hashlib
import json

import pytest

from mazhost import fw
from test_app import client

AUTH = {"Authorization": "Bearer test-token-that-is-not-default"}


def stage(tmp_path, monkeypatch, version="1.6.0"):
    monkeypatch.setenv("MAZ_FW_DIR", str(tmp_path))
    data = b"staged firmware"
    manifest = {"version": version, "sha": "abcdef1", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    (tmp_path / "latest.bin").write_bytes(data)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return manifest


@pytest.mark.parametrize("running,available", [
    ("1.5.1", True), ("1.6.0", False), ("1.7.0", False),
    ("1.10.0", False), ("1.5", True), ("", False), ("bad", False),
    ("9" * 5000 + ".1", False),
])
def test_staged_status_compares_numeric_versions(tmp_path, monkeypatch, running, available):
    manifest = stage(tmp_path, monkeypatch)
    result = client().get("/fw/status", params={"version": running}, headers=AUTH)
    assert result.status_code == 200
    body = result.json()
    assert body["update_available"] is available
    assert body["staged"] == {**manifest, "staged_at": body["staged"]["staged_at"]}
    assert body["staged"]["staged_at"].endswith("+00:00")
    assert client().get("/fw/status").status_code in (401, 403)


@pytest.mark.parametrize("damage", ["missing", "size", "hash", "json", "list", "version"])
def test_unusable_stage_never_offers_update(tmp_path, monkeypatch, damage):
    stage(tmp_path, monkeypatch)
    if damage == "missing":
        (tmp_path / "latest.bin").unlink()
    elif damage == "size":
        (tmp_path / "latest.bin").write_bytes(b"short")
    elif damage == "hash":
        (tmp_path / "latest.bin").write_bytes(b"broken firmware")
    else:
        (tmp_path / "manifest.json").write_text({"json": "{", "list": "[]", "version": '{"version":"bad"}'}[damage])
    assert fw.staged_status("1.5.1") == {"staged": None, "update_available": False}


def test_health_uses_core_decision_and_no_stage_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("MAZ_FW_DIR", str(tmp_path))
    api = client()
    assert api.get("/fw/status", headers=AUTH).json() == {"staged": None, "update_available": False}
    stage(tmp_path, monkeypatch)
    for version, expected in [("1.5.1", True), ("1.6.0", False), ("1.7.0", False), ("", False)]:
        body = api.get("/health", params={"fw_version": version}, headers=AUTH).json()
        assert body["fw_update_available"] is expected
    assert "fw_update_available" not in api.get("/health?fw_version=1.5.1").json()


def test_pusher_tells_device_once_per_change(tmp_path, monkeypatch):
    stage(tmp_path, monkeypatch)
    sent = []
    pusher = fw.FwPusher(lambda action: sent.append(action) or {"ok": True})
    assert pusher.tick("192.168.1.9") and not pusher.tick("192.168.1.9")
    assert pusher.tick("192.168.1.10")  # device appeared at a new address
    stage(tmp_path, monkeypatch, version="1.6.1")
    assert pusher.tick("192.168.1.10") and sent == ["fwcheck"] * 3


def test_pusher_retries_when_device_unreachable_and_never_raises(tmp_path, monkeypatch):
    stage(tmp_path, monkeypatch)
    pusher = fw.FwPusher(lambda action: {"ok": False})
    assert not pusher.tick() and not pusher.tick()
    pusher.send = lambda action: (_ for _ in ()).throw(RuntimeError("down"))
    assert not pusher.tick()
    pusher.send = lambda action: {"ok": True}
    assert pusher.tick()


def test_no_staged_firmware_pushes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MAZ_FW_DIR", str(tmp_path))
    assert not fw.FwPusher(lambda action: {"ok": True}).tick()
