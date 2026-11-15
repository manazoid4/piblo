from __future__ import annotations

import json

import httpx
from mazhost import netpool
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost.app import create_app
from mazhost.authority import AuthorityBroker
from mazhost.config import Settings
from mazhost.phone_control import build_phone_app
from mazhost.tts import SpeechOut
from mazhost.voices import ADRIAN_ID, CURATED, ID_RE, VoiceService

TOKEN = "test-token-that-is-not-default"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
OTHER = "933563129e564b19a115bedd57b7406a"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 40 + b"data" + b"\x01" * 100


def _settings(tmp_path, **kw):
    return Settings(_env_file=None, token=TOKEN, data_dir=str(tmp_path / "data"),
                    work_dir=str(tmp_path / "work"), control_dir=str(tmp_path / "control"),
                    debug_dir=str(tmp_path / "debug"), **kw)


class Resp:
    def __init__(self, status=200, content=b"", payload=None):
        self.status_code, self.content, self._p = status, content, payload
        self.text = str(payload or "")

    def json(self):
        return self._p


@pytest.fixture
def calls(monkeypatch):
    log = {"post": [], "get": []}

    def post(url, headers=None, json=None, timeout=None):
        log["post"].append(json["reference_id"])
        if json["reference_id"] == "f" * 32:
            return Resp(404, b"nope")
        return Resp(200, WAV)

    def get(url, headers=None, params=None, timeout=None):
        log["get"].append(params["title"])
        return Resp(200, payload={"items": [
            {"_id": OTHER, "type": "tts", "title": "Sarah", "description": "An engaged speaker.", "visibility": "public", "tags": ["female"]},
            {"_id": "a" * 32, "type": "tts", "title": "Hidden", "visibility": "private"},
            {"_id": "bad", "type": "tts", "title": "BadId", "visibility": "public"},
        ]})

    monkeypatch.setattr(netpool.client, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    return log


def mk(tmp_path, key="k-secret"):
    s = _settings(tmp_path, fish_api_key=key, tts_enabled=True)
    return TestClient(create_app(s)), s


def test_curated_ids_wellformed_and_include_adrian():
    ids = [v["reference_id"] for v in CURATED]
    assert len(ids) == len(set(ids)) >= 8
    assert all(ID_RE.match(i) for i in ids)
    assert ADRIAN_ID in ids


def test_voices_require_auth(tmp_path):
    c, _ = mk(tmp_path)
    assert c.get("/voices").status_code == 401
    assert c.post("/voices/select", json={"reference_id": OTHER}).status_code == 401


def test_list_default_and_select_persists(tmp_path, calls):
    c, s = mk(tmp_path)
    d = c.get("/voices", headers=AUTH).json()
    assert d["current"] == ADRIAN_ID and d["current_name"] == "Adrian"
    assert [v["reference_id"] for v in d["voices"] if v["current"]] == [ADRIAN_ID]
    assert c.post("/voices/select", json={"reference_id": OTHER}, headers=AUTH).json()["current"] == OTHER
    assert json.loads((tmp_path / "data" / "settings.json").read_text())["voice_reference_id"] == OTHER
    # a fresh Core on the same data dir sees it
    c2 = TestClient(create_app(s))
    assert c2.get("/voices", headers=AUTH).json()["current"] == OTHER
    assert c.post("/voices/select", json={"reference_id": "../x"}, headers=AUTH).status_code == 400


def test_search_proxy_filters_and_caches(tmp_path, calls):
    c, _ = mk(tmp_path)
    r = c.get("/voices/search?q=Sarah", headers=AUTH).json()
    assert [v["reference_id"] for v in r["voices"]] == [OTHER]
    c.get("/voices/search?q=sarah", headers=AUTH)
    assert len(calls["get"]) == 1  # cached 10 min


def test_search_needs_key(tmp_path, calls):
    c, _ = mk(tmp_path, key="")
    assert c.get("/voices/search?q=x", headers=AUTH).status_code == 503


def test_preview_cached_on_disk(tmp_path, calls):
    c, _ = mk(tmp_path)
    r = c.post("/voices/preview", json={"reference_id": OTHER}, headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    c.post("/voices/preview", json={"reference_id": OTHER}, headers=AUTH)
    assert calls["post"] == [OTHER]  # second one served from disk
    assert (tmp_path / "data" / "voice-previews" / f"{OTHER}.wav").exists()
    assert c.post("/voices/preview", json={"reference_id": "zz"}, headers=AUTH).status_code == 400


def test_tts_uses_selected_voice_and_falls_back_once(tmp_path, calls):
    s = _settings(tmp_path, fish_api_key="k", tts_enabled=True)
    out = SpeechOut(s)
    out.voices.select(OTHER)
    out.synthesize("hello").unlink()
    assert calls["post"][-1] == OTHER
    out.voices.select("f" * 32)
    out.synthesize("hello").unlink()
    assert calls["post"][-2:] == ["f" * 32, ADRIAN_ID]
    assert out.voices.current() == ADRIAN_ID  # bad id no longer selected
    assert out.last_provider == "fish"


def test_invalid_stored_id_falls_back_to_adrian(tmp_path):
    s = _settings(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text('{"voice_reference_id": "garbage"}')
    assert VoiceService(s).current() == ADRIAN_ID


def test_phone_routes_need_session_and_work(tmp_path, calls):
    s = _settings(tmp_path, fish_api_key="k", tts_enabled=True)
    svc = SpeechOut(s).voices
    root = FastAPI()
    root.mount("/control", build_phone_app(s, AuthorityBroker(s), voices=svc))
    anon = TestClient(root, base_url="http://testserver/control/", headers={"X-MAZ-Control": "1"})
    assert anon.get("api/voices").status_code == 401
    c = TestClient(root, base_url="http://testserver/control/", headers={"user-agent": "ph"})
    c.post("session/login", data={"token": TOKEN})
    c.headers.update({"X-MAZ-Control": "1"})
    assert c.get("api/voices").json()["current"] == ADRIAN_ID
    assert c.post("api/voices/select", json={"reference_id": OTHER}).json()["current"] == OTHER
    assert c.get("api/voices/search?q=sarah").json()["voices"][0]["reference_id"] == OTHER
    assert c.post("api/voices/preview", json={"reference_id": OTHER}).content[:4] == b"RIFF"
    assert "pane-voice" in c.get("").text


def test_say_uses_free_model_and_validates(tmp_path, monkeypatch):
    seen = []

    def post(url, headers=None, json=None, timeout=None):
        seen.append((headers["model"], json["text"], json["reference_id"]))
        return Resp(200, WAV)

    monkeypatch.setattr(netpool.client, "post", post)
    c, s = mk(tmp_path)
    r = c.post("/say", json={"text": "  hello   there "}, headers=AUTH)
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert seen == [(s.tts_model, "hello there", ADRIAN_ID)]
    assert s.tts_model == "s2.1-pro-free"  # free model only
    assert c.post("/say", json={"text": "hi", "voice": OTHER}, headers=AUTH).status_code == 200
    assert seen[-1][2] == OTHER and seen[-1][0] == "s2.1-pro-free"
    for bad in ({"text": "   "}, {"text": "x" * 301}, {"text": "hi", "voice": "nope"}):
        assert c.post("/say", json=bad, headers=AUTH).status_code == 400
    assert c.post("/say", json={"text": "hi"}).status_code == 401
    assert len(seen) == 2


def test_say_on_phone_page(tmp_path, calls):
    s = _settings(tmp_path, fish_api_key="k", tts_enabled=True)
    root = FastAPI()
    root.mount("/control", build_phone_app(s, AuthorityBroker(s), voices=SpeechOut(s).voices))
    c = TestClient(root, base_url="http://testserver/control/", headers={"user-agent": "ph"})
    c.post("session/login", data={"token": TOKEN})
    c.headers.update({"X-MAZ-Control": "1"})
    assert c.post("api/say", json={"text": "hello"}).content[:4] == b"RIFF"
    assert 'id="sayText"' in c.get("").text


def test_say_falls_back_to_local_tts_when_fish_fails(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mazhost.voices import VoiceService, install_voice_routes

    def no_fish(text, path, voice):
        raise RuntimeError("no_fish_key")

    local = tmp_path / "local.wav"
    local.write_bytes(b"RIFFlocal")
    svc = VoiceService.__new__(VoiceService)
    svc._synth, svc.fallback = no_fish, lambda text: local
    svc.current = lambda: "a" * 32
    app = FastAPI()
    install_voice_routes(app, svc)
    r = TestClient(app).post("/say", json={"text": "hello"})
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
