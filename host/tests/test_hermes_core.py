"""Piblo Core with Hermes chosen as the agent, end to end against the SDK's real gadget hub."""

from __future__ import annotations

import hashlib
import hmac
import io
import math
import struct
import time
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from hermes_fixture import hermes_gateway  # noqa: F401  (fixture)
from mazhost import buddy as buddy_mod, private_mode
from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.llm import Models

DEVICE_KEY = "piblo-device-test-key"


def wav(seconds: float = 1.0) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(i / 8))) for i in range(int(16000 * seconds))))
    return out.getvalue()


class FakeStt:
    def available(self) -> bool:
        return True

    def transcribe(self, _path) -> str:
        return "whats the weather on mars"


@pytest.fixture(autouse=True)
def device_key(monkeypatch):
    monkeypatch.setenv("PIBLO_ASK_DEVICE_KEY", DEVICE_KEY)
    before = private_mode.enabled()
    private_mode.set_enabled(False)
    yield
    private_mode.set_enabled(before)


def make_core(tmp_path, url, monkeypatch, **over):
    brain_calls = []
    cfg = Settings(_env_file=None, token="hermes-core-test-token", data_dir=str(tmp_path / "core"),
                   work_dir=str(tmp_path / "work"), control_dir=str(tmp_path / "control"),
                   debug_dir=str(tmp_path / "debug"), now_path="", core_enabled=False, buddy_db="off",
                   autoupdate=False, remote_detect=False, discovery_port=0,
                   hermes_enabled=True, agent="hermes", hermes_url=url, hermes_timeout_s=10, **over)
    speech = FakeStt()  # no model warm-up at startup (Parakeet loads ~4 GB)

    def local_brain(request):
        brain_calls.append(request.content.decode("utf-8", "replace"))  # startup warm-up also lands here
        return httpx.Response(200, json={"message": {"content": "Piblo's own brain here."}})

    models = Models(cfg, client=httpx.Client(transport=httpx.MockTransport(local_brain)))
    client = TestClient(create_app(cfg, stt=speech, models=models))
    return client, {"Authorization": "Bearer " + cfg.token}, brain_calls


@pytest.fixture
def core(hermes_gateway, tmp_path, monkeypatch):
    """Core connected and paired to a real gadget hub: (client, auth, hub, brain, lt, brain_calls)."""
    opened = []

    def make(pair: bool = True, **over):
        hub, brain, url, lt = hermes_gateway()
        client, auth, calls = make_core(tmp_path, url, monkeypatch, **over)
        client.__enter__()  # runs Core's startup: the Hermes link connects
        opened.append(client)
        status = wait(lambda: client.get("/hermes/status", headers=auth).json(), lambda s: s["pairing"] or s["paired"])
        if pair:
            assert lt.run(brain.approve(status["pairing"]["code"]))
            wait(lambda: client.get("/hermes/status", headers=auth).json(), lambda s: s["paired"])
        return client, auth, hub, brain, lt, calls

    yield make
    for client in opened:
        client.__exit__(None, None, None)


def wait(get, ok, timeout=10.0):
    end = time.monotonic() + timeout
    while True:
        value = get()
        if ok(value) or time.monotonic() > end:
            assert ok(value), value
            return value
        time.sleep(0.05)


def session(client, auth):
    return client.post("/session/start", headers=auth).json()["session_id"]


def text_turn(client, auth, sid, text):
    r = client.post("/turn/text", headers=auth, json={"session_id": sid, "text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_question_reaches_hermes_and_the_answer_returns(core):
    client, auth, hub, brain, lt, calls = core()
    body = text_turn(client, auth, session(client, auth), "plan my week")
    assert body["provider"] == "hermes" and body["reply"] == "Hermes says: plan my week"
    assert brain.texts == ["plan my week"] and not any("plan my week" in c for c in calls)  # Piblo's model not asked


def test_piblo_local_commands_still_answer_first(core):
    client, auth, hub, brain, lt, calls = core()
    body = text_turn(client, auth, session(client, auth), "remember the bins go out on Tuesday")
    assert body["provider"] == "memory-local" and brain.texts == []


def test_push_to_talk_core_stt_then_hermes(core):
    client, auth, hub, brain, lt, calls = core()
    sid = session(client, auth)
    r = client.post("/turn/raw", headers={**auth, "X-MAZ-Session": sid}, content=wav())
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "Hermes says: whats the weather on mars"


def test_push_to_talk_audio_goes_to_hermes_stt(core):
    client, auth, hub, brain, lt, calls = core(hermes_stt="hermes")
    sid = session(client, auth)
    r = client.post("/turn/raw", headers={**auth, "X-MAZ-Session": sid}, content=wav(1.5))
    body = r.json()
    assert r.status_code == 200 and body["provider"] == "hermes", body
    assert body["reply"] == "Heard 1.5 seconds" and body["text"] == "what time is it"
    assert brain.utterances and brain.utterances[0][0] == 1.5


def test_switch_back_to_piblo_brain(core):
    client, auth, hub, brain, lt, calls = core()
    assert client.post("/hermes/agent", headers=auth, json={"agent": "piblo"}).json()["selected"] is False
    body = text_turn(client, auth, session(client, auth), "tell me a joke")
    assert body["provider"] != "hermes" and brain.texts == [] and any("tell me a joke" in c for c in calls)


def test_unpaired_shows_code_on_piblo_and_says_how(core):
    client, auth, hub, brain, lt, calls = core(pair=False)
    body = text_turn(client, auth, session(client, auth), "hello")
    assert body["degraded"] and "hermes gadget pair" in body["reply"] and "A B C D 2 3 4 5" in body["reply"]
    alerts = client.get("/buddy/alerts", headers=auth).json()
    assert any(a["title"] == "Pair Hermes: ABCD2345" for a in alerts.get("alerts", alerts.get("items", [])))
    assert brain.texts == []


def test_card_becomes_a_piblo_alert(core):
    client, auth, hub, brain, lt, calls = core()
    body = text_turn(client, auth, session(client, auth), "card")
    assert body["cards"][0]["items"] == ["Milk", "Eggs"]
    alerts = client.get("/buddy/alerts", headers=auth).json()
    rows = alerts.get("alerts", alerts.get("items", []))
    card = next(a for a in rows if a["title"] == "Shopping")
    assert card["body"] == "This week\n40%\n- Milk\n- Eggs" and card["source"] == "hermes"


def pending(client, auth):
    return client.get("/buddy/summary", headers=auth).json()["items"]


def proof(item, decision="allow"):
    msg = f"{item['id']}:{item['digest']}:{decision}:{item['nonce']}".encode()
    return hmac.new(DEVICE_KEY.encode(), msg, hashlib.sha256).hexdigest()


def test_approval_needs_the_devices_signed_allow(core):
    client, auth, hub, brain, lt, calls = core()
    text_turn(client, auth, session(client, auth), "run rm -rf build")
    item = wait(lambda: pending(client, auth), bool)[0]
    assert item["agent"] == "Hermes" and item["sensitive"] and "rm -rf build" in item["summary"]
    # Allow without the device's proof is refused, and Hermes hears nothing
    r = client.post("/buddy/decide", headers=auth, json={"id": item["id"], "decision": "allow"})
    assert r.status_code == 403
    time.sleep(0.3)
    assert brain.answers == []
    r = client.post("/buddy/decide", headers=auth, json={"id": item["id"], "decision": "allow", "proof": proof(item)})
    assert r.status_code == 200, r.text
    wait(lambda: brain.answers, bool)
    assert brain.answers == [("q-rm -rf build", True)]


def test_deny_on_piblo_sends_no(core):
    client, auth, hub, brain, lt, calls = core()
    text_turn(client, auth, session(client, auth), "run shutdown")
    item = wait(lambda: pending(client, auth), bool)[0]
    client.post("/buddy/decide", headers=auth, json={"id": item["id"], "decision": "deny"})
    wait(lambda: brain.answers, bool)
    assert brain.answers == [("q-shutdown", False)]


def test_expired_piblo_approval_denies_and_cannot_be_allowed_late(core, monkeypatch):
    monkeypatch.setattr(buddy_mod, "TIMEOUT_S", 1)
    client, auth, hub, brain, lt, calls = core()
    text_turn(client, auth, session(client, auth), "run deploy")
    item = wait(lambda: pending(client, auth), bool)[0]
    wait(lambda: brain.answers, bool, timeout=6)
    assert brain.answers == [("q-deploy", False)]
    late = client.post("/buddy/decide", headers=auth, json={"id": item["id"], "decision": "allow", "proof": proof(item)})
    assert late.status_code == 409


def test_replayed_question_after_reconnect_is_not_reapproved(core):
    client, auth, hub, brain, lt, calls = core()
    text_turn(client, auth, session(client, auth), "run reboot")
    item = wait(lambda: pending(client, auth), bool)[0]
    device_id = client.get("/hermes/status", headers=auth).json()["device_id"]
    connects = client.get("/hermes/status", headers=auth).json()["connects"]
    lt.run(hub.get(device_id).close("network blip"))
    wait(lambda: pending(client, auth), lambda p: not p)  # withdrawn from Piblo's screen: fail closed
    wait(lambda: client.get("/hermes/status", headers=auth).json(), lambda s: s["connected"] and s["connects"] > connects)
    # Hermes asks the same question again on the new connection: Piblo answers no, never yes
    lt.run(hub.get(device_id).ask("q-reboot", "Allow this command?", "reboot", ttl_s=5))
    wait(lambda: brain.answers, bool)
    assert brain.answers == [("q-reboot", False)]
    r = client.post("/buddy/decide", headers=auth, json={"id": item["id"], "decision": "allow", "proof": proof(item)})
    assert r.status_code != 200 or r.json()["decision"] != "allow"


def test_hermes_withdrawing_a_question_clears_it_on_piblo(core):
    client, auth, hub, brain, lt, calls = core()
    text_turn(client, auth, session(client, auth), "run backup")
    wait(lambda: pending(client, auth), bool)
    device_id = client.get("/hermes/status", headers=auth).json()["device_id"]
    lt.run(hub.get(device_id).close_prompt("q-backup"))
    wait(lambda: pending(client, auth), lambda p: not p)
    assert brain.answers == []


def test_gateway_down_is_a_clear_spoken_state(tmp_path, monkeypatch):
    client, auth, calls = make_core(tmp_path, "ws://127.0.0.1:9/gadget", monkeypatch)
    with client:
        body = text_turn(client, auth, session(client, auth), "hello")
        assert body["provider"] == "hermes" and body["degraded"] and "isn't reachable" in body["reply"]
        status = client.get("/hermes/status", headers=auth).json()
        assert status["enabled"] and not status["connected"]


def test_hermes_off_by_default_changes_nothing(tmp_path, monkeypatch):
    cfg_off = Settings(_env_file=None, token="t-off", data_dir=str(tmp_path / "off"), now_path="",
                       core_enabled=False, buddy_db="off", autoupdate=False, remote_detect=False, discovery_port=0)
    off = TestClient(create_app(cfg_off, stt=FakeStt()))
    status = off.get("/hermes/status", headers={"Authorization": "Bearer t-off"}).json()
    assert status["enabled"] is False and status["agent"] == "piblo" and status["selected"] is False
    assert off.post("/hermes/ask", headers={"Authorization": "Bearer t-off"}, json={"text": "hi"}).status_code == 503


def test_private_mode_never_routes_to_hermes(core):
    client, auth, hub, brain, lt, calls = core()
    private_mode.set_enabled(True)
    try:
        assert client.get("/hermes/status", headers=auth).json()["selected"] is False
    finally:
        private_mode.set_enabled(False)


def test_hermes_routes_need_the_core_token(core):
    client, auth, hub, brain, lt, calls = core()
    assert client.get("/hermes/status").status_code in (401, 403)
    assert client.post("/hermes/agent", json={"agent": "hermes"}).status_code in (401, 403)


def test_hermes_timeout_withdraws_the_piblo_approval(core):
    client, auth, hub, brain, lt, calls = core()
    brain.prompt_ttl = 1  # Hermes gives up before Piblo's own 60 s expiry
    text_turn(client, auth, session(client, auth), "run migrate")
    item = wait(lambda: pending(client, auth), bool)[0]
    wait(lambda: pending(client, auth), lambda p: not p, timeout=5)
    late = client.post("/buddy/decide", headers=auth, json={"id": item["id"], "decision": "allow", "proof": proof(item)})
    assert late.status_code != 200 or late.json()["decision"] != "allow"
    time.sleep(0.3)
    assert brain.answers == []
