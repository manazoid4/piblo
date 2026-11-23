import hashlib
import json

import pytest

from test_app import client

AUTH = {"Authorization": "Bearer test-token-that-is-not-default"}


def stage(tmp_path, monkeypatch, data=b"fake apk bytes"):
    monkeypatch.setenv("MAZ_PHONE_DIR", str(tmp_path))
    m = {"version_code": 142, "version_name": "0.2.0-beta+ci42", "label": "PR #222", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "staged_at": "2026-11-23T23:00:00+00:00"}
    (tmp_path / "piblo.apk").write_bytes(data)
    (tmp_path / "manifest.json").write_text(json.dumps(m))
    return m


def test_staged_build_is_offered_and_downloads(tmp_path, monkeypatch):
    m = stage(tmp_path, monkeypatch)
    r = client().get("/phone/apk/manifest", headers=AUTH)
    assert r.status_code == 200 and r.json() == m
    apk = client().get("/phone/apk/latest.apk", headers=AUTH)
    assert apk.status_code == 200 and apk.content == b"fake apk bytes"
    assert apk.headers["content-type"] == "application/vnd.android.package-archive"


def test_needs_auth(tmp_path, monkeypatch):
    stage(tmp_path, monkeypatch)
    assert client().get("/phone/apk/manifest").status_code in (401, 403)
    assert client().get("/phone/apk/latest.apk").status_code in (401, 403)


@pytest.mark.parametrize("damage", ["none", "missing_apk", "size", "hash", "json", "no_code"])
def test_unusable_stage_is_never_offered(tmp_path, monkeypatch, damage):
    if damage == "none":
        monkeypatch.setenv("MAZ_PHONE_DIR", str(tmp_path / "empty"))
    else:
        m = stage(tmp_path, monkeypatch)
        if damage == "missing_apk": (tmp_path / "piblo.apk").unlink()
        if damage == "size": (tmp_path / "piblo.apk").write_bytes(b"other length bytes!")
        if damage == "hash": (tmp_path / "piblo.apk").write_bytes(b"fake apk bytez")
        if damage == "json": (tmp_path / "manifest.json").write_text("{nope")
        if damage == "no_code": (tmp_path / "manifest.json").write_text(json.dumps({**m, "version_code": "142"}))
    assert client().get("/phone/apk/manifest", headers=AUTH).status_code == 404
    assert client().get("/phone/apk/latest.apk", headers=AUTH).status_code == 404


def test_stage_script_output_is_served(tmp_path, monkeypatch):
    import subprocess, sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "scripts" / "stage_phone_apk.py"
    apk = tmp_path / "in.apk"
    apk.write_bytes(b"PK\x03\x04 pretend apk")
    out = tmp_path / "phone"
    subprocess.run([sys.executable, str(script), "--file", str(apk), "--code", "150", "--label", "test", "--out", str(out)], check=True, capture_output=True)
    monkeypatch.setenv("MAZ_PHONE_DIR", str(out))
    r = client().get("/phone/apk/manifest", headers=AUTH)
    assert r.status_code == 200 and r.json()["version_code"] == 150 and r.json()["label"] == "test"
    assert client().get("/phone/apk/latest.apk", headers=AUTH).content == apk.read_bytes()
    bad = tmp_path / "bad.apk"; bad.write_bytes(b"not a zip")
    assert subprocess.run([sys.executable, str(script), "--file", str(bad), "--code", "151", "--out", str(out)], capture_output=True).returncode != 0
