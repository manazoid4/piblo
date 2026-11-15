from __future__ import annotations

import io
import tempfile
import wave
from types import SimpleNamespace

from fastapi.testclient import TestClient

from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.errors import ErrorCode, RouteError


class FakeStt:
    def available(self):
        return True

    def transcribe(self, _path):
        return "what should I focus on"


class FakeModels:
    def __init__(self):
        self.last_messages = []
        self.last_route = None

    def status(self):
        return {"local": True, "cloud": False, "ai_profile": "smart"}

    def diagnostics(self, preferred_route):
        return {
            "preferred_route": preferred_route,
            "active_route": self.last_route,
            "last_route": {},
            "9router": {"reachable": True, "authenticated": True, "auth_status": "accepted"},
            "mazlatest": {"configured_aliases": ["MazLatest"], "model_available": True, "available": True},
            "cloud": {"model": "test/model", "model_available": True, "available": True},
            "local": {"engine": "llamacpp", "endpoint_available": False, "model": "local", "model_available": False, "available": False},
            "auto": {"available": True, "degraded": True, "fallback": "cloud"},
        }

    def chat(self, messages, route):
        self.last_messages = messages
        self.last_route = route
        from mazhost.llm import output_cap
        self.last_cap = output_cap()
        prior = sum(message["role"] == "assistant" for message in messages)
        return f"Focus on the hardware test. Prior replies: {prior}", "local"


class FakeNudge:
    def status(self):
        return {"configured": True, "online": True}

    def summary(self):
        return {"state": "ALL_SYNCED", "agents": []}

    def detail(self, session_id):
        return {"sessionId": session_id, "state": "ALL_SYNCED"}

    def nudge(self, session_id):
        return {"sessionId": session_id, "queued": True}


class FakePC:
    available = True

    def __init__(self):
        self.actions: list[str] = []

    def perform(self, action: str):
        self.actions.append(action)
        return SimpleNamespace(action=action, label=f"did {action}")


class FakeBeam:
    def __init__(self):
        self.queue = []
        self.incoming = []

    def to_pocket(self, text):
        item = {"id": "beam1", "text": text, "kind": "link" if text.startswith("http") else "text"}
        self.queue.append(item)
        return item

    def pull(self):
        return self.queue.pop(0) if self.queue else None

    def from_pocket(self, text):
        item = {"id": "beam2", "text": text, "kind": "text", "clipboard": True}
        self.incoming.append(item)
        return item

    def history(self, limit=20):
        return (self.queue + self.incoming)[-limit:]


class FakeTelemetry:
    def snapshot(self):
        return {
            "ok": True,
            "cpu_pct": 12.5,
            "ram_pct": 40.0,
            "gpu": {"available": True, "util_pct": 22, "vram_used_mb": 3100, "vram_total_mb": 6144, "temp_c": 58},
            "ollama": {"online": True, "loaded": True, "model": "qwen3.5:4b", "vram_mb": 2800, "context": 4096},
        }


class FakeCore:
    def status(self):
        return {"ok": True}

    def cardputer_status(self):
        return {"ok": True, "route": "mazlatest"}

    def context_for_prompt(self, _text):
        return ""


def client(pc=None, models=None, beam=None, telemetry=None, core=None, work_dir=None):
    settings_kwargs = {"token": "test-token-that-is-not-default", "_env_file": None}
    if work_dir is not None:
        settings_kwargs["work_dir"] = str(work_dir)
        settings_kwargs["control_dir"] = str(work_dir / "control")
        settings_kwargs["debug_dir"] = str(work_dir / "debug")
    settings_kwargs["project_roots"] = tempfile.mkdtemp()  # startup project scan must not walk the real repos
    settings = Settings(**settings_kwargs)
    return TestClient(
        create_app(
            settings,
            stt=FakeStt(),
            models=models or FakeModels(),
            nudge=FakeNudge(),
            pc=pc or FakePC(),
            core=core,
            beam=beam or FakeBeam(),
            telemetry=telemetry or FakeTelemetry(),
        )
    )


def test_requires_bearer_token():
    assert client().get("/models").status_code == 401
    # /health is public but minimal without the token (safe to expose remotely).
    public = client().get("/health")
    assert public.status_code == 200 and set(public.json()) == {"ok", "name", "version"}


def test_text_turn_keeps_session_context_and_nudge_is_evidence_backed():
    api = client()
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]

    first = api.post(
        "/turn/text",
        headers=headers,
        json={"session_id": sid, "route": "auto", "text": "What should I focus on?"},
    )
    second = api.post(
        "/turn/text",
        headers=headers,
        json={"session_id": sid, "route": "auto", "text": "Are my agents synced?"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["reply"].endswith("Prior replies: 1")
    assert api.get("/nudge", headers=headers).json()["state"] == "ALL_SYNCED"


def test_text_turn_accepts_explicit_mazlatest_route():
    models = FakeModels()
    api = client(models=models)
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]

    response = api.post(
        "/turn/text",
        headers=headers,
        json={"session_id": sid, "route": "mazlatest", "text": "Use the routed model"},
    )

    assert response.status_code == 200
    assert models.last_route == "mazlatest"


def test_what_should_i_do_today_is_traced_to_work_state_not_model(tmp_path):
    from mazhost.work_store import WorkStore

    work_dir = tmp_path / "work"
    store = WorkStore(work_dir)
    store.bootstrap()
    store.put_pipeline_item({
        "item_id": "client_daily", "pipeline": "client", "title": "Acme",
        "organisation": "Acme Ltd", "stage": "QUALIFIED", "score": 88,
        "proof_status": "IN_PROGRESS", "evidence": "Public form reviewed",
        "next_action": "Finish Acme workflow mock-up",
    })
    models = FakeModels()
    api = client(models=models, work_dir=work_dir)
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]

    response = api.post(
        "/turn/text", headers=headers,
        json={"session_id": sid, "route": "auto", "text": "What should I do today?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "work-state-local"
    assert body["timings"]["llm_ms"] == 0
    assert "Finish Acme workflow mock-up" in body["reply"]
    assert body["work_state"]["recommendations"][0]["item_id"] == "client_daily"
    assert models.last_messages == []


def test_diagnostics_is_authenticated_safe_and_distinguishes_route_state():
    api = client(core=FakeCore())
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}

    assert api.get("/diagnostics").status_code == 401
    response = api.get("/diagnostics", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["routes"]["9router"]["authenticated"] is True
    assert body["routes"]["mazlatest"]["model_available"] is True
    assert body["routes"]["auto"]["degraded"] is True
    assert body["cardputer"]["connected"] is True
    serialized = response.text.lower()
    assert "bearer" not in serialized
    assert "test-token" not in serialized


def test_typed_route_error_is_returned_at_top_level():
    class FailingModels(FakeModels):
        def chat(self, _messages, _route):
            raise RouteError(
                ErrorCode.MODEL_NOT_FOUND,
                "cloud",
                upstream_status=404,
                retryable=False,
                model="retired/model",
            )

    api = client(models=FailingModels(), core=FakeCore())
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]
    response = api.post(
        "/turn/text",
        headers=headers,
        json={"session_id": sid, "route": "cloud", "text": "hello"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "ok": False,
        "route": "cloud",
        "error": "MODEL_NOT_FOUND",
        "retryable": False,
        "upstream_status": 404,
        "model": "retired/model",
    }


def test_all_provider_failure_returns_explicit_degraded_state_never_500():
    class AllFailingModels(FakeModels):
        def chat(self, _messages, _route):
            raise RouteError(
                ErrorCode.LOCAL_UNAVAILABLE,
                "degraded",
                retryable=True,
                requested_route="auto",
            )

    api = client(models=AllFailingModels(), core=FakeCore())
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]
    response = api.post(
        "/turn/text",
        headers=headers,
        json={"session_id": sid, "route": "auto", "text": "hello"},
    )

    # AUTO chain exhausted: still 200 with a short spoken fallback so /speak says something.
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["reply"] == "Sorry, my brain is offline, try again"


def test_context_ask_is_added_as_untrusted_screen_evidence():
    models = FakeModels()
    api = client(models=models)
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]
    response = api.post(
        "/turn/text",
        headers=headers,
        json={
            "session_id": sid,
            "route": "local",
            "text": "what should I do next?",
            "context": "surface=RECALL selected=festival packing note",
        },
    )
    assert response.status_code == 200
    system = models.last_messages[-1]["content"]
    assert "current-screen context" in system
    assert "festival packing note" in system
    assert "never as instructions" in system


def test_context_ask_never_executes_a_pc_command():
    pc = FakePC()
    models = FakeModels()
    api = client(pc=pc, models=models)
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]
    response = api.post(
        "/turn/text",
        headers=headers,
        json={
            "session_id": sid,
            "route": "local",
            "text": "lock the pc",
            "context": "surface=RECALL selected=lock-screen checklist",
        },
    )
    assert response.status_code == 200
    assert response.json()["provider"] == "local"
    assert pc.actions == []


def test_field_endpoints_are_authenticated_and_bounded():
    beam = FakeBeam()
    api = client(beam=beam)
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}

    telemetry = api.get("/system/status", headers=headers)
    assert telemetry.status_code == 200
    assert telemetry.json()["gpu"]["vram_total_mb"] == 6144

    queued = api.post("/beam/to-pocket", headers=headers, json={"text": "https://example.com"})
    assert queued.status_code == 200
    pulled = api.get("/beam/pull", headers=headers)
    assert pulled.json()["message"]["kind"] == "link"

    sent = api.post("/beam/from-pocket", headers=headers, json={"text": "hello laptop"})
    assert sent.status_code == 200
    assert sent.json()["reply"] == "BEAMED TO LAPTOP"

    assert api.get("/system/status").status_code == 401
    assert api.get("/beam/pull").status_code == 401


def test_pc_control_is_allowlisted_and_can_skip_the_llm():
    pc = FakePC()
    api = client(pc)
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]

    direct = api.post("/pc/action", headers=headers, json={"action": "mute"})
    spoken = api.post(
        "/turn/text",
        headers=headers,
        json={"session_id": sid, "route": "auto", "text": "show desktop"},
    )

    assert direct.status_code == 200
    assert direct.json()["provider"] == "pc-local"
    assert spoken.status_code == 200
    assert spoken.json()["provider"] == "pc-local"
    assert spoken.json()["timings"]["llm_ms"] == 0
    assert pc.actions == ["mute", "desktop"]


def test_transcribe_raw_returns_only_the_words():
    """Dictation must not answer, and must not join the conversation."""
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 160)

    api = client()
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    response = api.post(
        "/transcribe/raw",
        content=audio.getvalue(),
        headers={**headers, "Content-Type": "audio/wav"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "what should I focus on"
    assert "reply" not in body
    assert "Focus on the hardware test" not in str(body)


def test_transcribe_raw_needs_the_token():
    assert client().post("/transcribe/raw", content=b"").status_code == 401


def test_raw_audio_turn_accepts_streamed_wav_and_context_header():
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 160)

    models = FakeModels()
    api = client(models=models)
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]
    response = api.post(
        "/turn/raw",
        content=audio.getvalue(),
        headers={
            **headers,
            "Content-Type": "audio/wav",
            "X-MAZ-Session": sid,
            "X-MAZ-Route": "local",
            "X-MAZ-Context": "surface=FLOW selected=SHIFT",
        },
    )

    assert response.status_code == 200
    assert response.json()["text"] == "what should I focus on"
    assert "selected=SHIFT" in models.last_messages[-1]["content"]


def test_auto_chain_failure_returns_spoken_fallback():
    class DeadModels(FakeModels):
        def chat(self, _messages, _route):
            raise RuntimeError("all providers down")

    api = client(models=DeadModels(), core=FakeCore())
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]
    response = api.post(
        "/turn/text", headers=headers, json={"session_id": sid, "route": "auto", "text": "hello"}
    )
    assert response.status_code == 200
    assert response.json()["reply"] == "Sorry, my brain is offline, try again"
    assert response.json()["degraded"] is True


def test_voice_token_cap_applies_only_to_the_spoken_path():
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 160)
    models = FakeModels()
    api = client(models=models)
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    sid = api.post("/session/start", headers=headers).json()["session_id"]

    def raw(context=""):
        api.post("/turn/raw", content=audio.getvalue(), headers={
            **headers, "Content-Type": "audio/wav", "X-MAZ-Session": sid, "X-MAZ-Route": "local",
            "X-MAZ-Context": context})
        return models.last_cap

    assert raw() == 60
    assert raw("surface=FLOW selected=SHIFT") == 160  # screen Ask is not a spoken reply
    api.post("/turn/text", headers=headers, json={"text": "hello", "session_id": sid, "route": "local"})
    assert models.last_cap == 160
