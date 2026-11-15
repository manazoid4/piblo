"""PTT must select local providers before any cloud function is entered."""
import io
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from mazhost import netpool, private_mode
from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.llm import Models
from mazhost.stt import SpeechToText
from mazhost.tts import SpeechOut


@pytest.fixture(autouse=True)
def restore_private_mode():
    before = private_mode.enabled()
    private_mode.set_enabled(False)
    yield
    private_mode.set_enabled(before)


def wav_bytes():
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(3200))
    return data.getvalue()


@pytest.mark.parametrize("private,keyed,route", [(True, True, "auto"), (False, True, "local"), (False, False, "auto")])
@pytest.mark.parametrize("endpoint", ["raw", "multipart", "text", "transcribe"])
def test_private_ptt_never_enters_cloud(tmp_path, monkeypatch, private, keyed, route, endpoint):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("cloud")
        raise AssertionError("cloud function entered")

    cfg = Settings(_env_file=None, token="private-ptt-test-token", private_mode=private,
                   data_dir=str(tmp_path), work_dir=str(tmp_path / "work"),
                   control_dir=str(tmp_path / "control"), debug_dir=str(tmp_path / "debug"),
                   now_path="", core_enabled=False, buddy_db="off", smart_voice=True,
                   groq_key="test-key" if keyed else "", fish_api_key="test-key" if keyed else "",
                   tts_enabled=True, autoupdate=False, remote_detect=False)
    speech = SpeechToText(cfg)
    monkeypatch.setattr(speech, "transcribe", lambda path: calls.append("stt-local") or "Hello Piblo")
    monkeypatch.setattr(speech, "_groq", forbidden)
    monkeypatch.setattr(netpool, "warm", forbidden)
    monkeypatch.setattr(SpeechOut, "_fish", forbidden)
    monkeypatch.setattr("mazhost.app.weather_line", forbidden)

    def local_tts(self, text, path):
        calls.append("kokoro")
        path.write_bytes(wav_bytes())

    def local_brain(request):
        assert request.url.host == "127.0.0.1"
        calls.append("brain-local")
        return httpx.Response(200, json={"message": {"content": "Hello from your local brain."}})

    monkeypatch.setattr(SpeechOut, "_kokoro", local_tts)
    models = Models(cfg, client=httpx.Client(transport=httpx.MockTransport(local_brain)))
    for name in ("_groq", "_cloud", "_mazlatest", "_openrouter"):
        monkeypatch.setattr(models, name, forbidden)
    api = TestClient(create_app(cfg, stt=speech, models=models))
    auth = {"Authorization": "Bearer " + cfg.token, "X-MAZ-Route": route}
    sid = api.post("/session/start", headers=auth).json()["session_id"]
    headers = {**auth, "X-MAZ-Session": sid, "X-MAZ-Speak": "1"}
    if endpoint == "transcribe":
        response = api.post("/transcribe/raw", headers=headers, content=wav_bytes())
        assert response.status_code == 200 and response.json()["transcript"] == "Hello Piblo"
    else:
        if endpoint == "raw":
            response = api.post("/turn/raw", headers=headers, content=wav_bytes())
        elif endpoint == "multipart":
            response = api.post("/turn", headers=headers, data={"session_id": sid, "route": route},
                                files={"audio": ("speech.wav", wav_bytes(), "audio/wav")})
        else:
            response = api.post("/turn/text", headers=headers,
                                json={"session_id": sid, "route": route, "text": "Hello Piblo"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provider"].startswith("local") and not body.get("degraded"), body
        audio = api.get("/speak", headers=auth, params={"id": body["speak"]["id"]})
        assert audio.status_code == 200 and audio.headers["X-TTS-Provider"] == "kokoro"
        assert audio.content == wav_bytes()
        assert "brain-local" in calls and "kokoro" in calls
        # Device fallback after a failed GET /speak must preserve the same route.
        classic = api.post("/speak", headers=auth, json={"text": "Classic local speech."})
        assert classic.status_code == 200 and classic.headers["X-TTS-Provider"] == "kokoro"
    assert "cloud" not in calls


def test_private_stt_skips_groq_even_with_key(tmp_path, monkeypatch):
    private_mode.set_enabled(True)
    speech = SpeechToText(Settings(_env_file=None, groq_key="test-key"))
    calls = []
    monkeypatch.setattr(speech, "_groq", lambda *args: calls.append("cloud") or "cloud")
    monkeypatch.setattr(speech, "transcribe", lambda path: "local")
    assert speech.transcribe_turn(tmp_path / "speech.wav") == "local"
    assert calls == []


def test_private_kokoro_failure_is_explicit(tmp_path, monkeypatch):
    private_mode.set_enabled(True)
    out = SpeechOut(Settings(_env_file=None, data_dir=str(tmp_path), tts_enabled=True, fish_api_key="test-key"))
    def missing(*args):
        raise RuntimeError("missing local model")
    monkeypatch.setattr(out, "_kokoro", missing)
    with pytest.raises(RuntimeError, match="kokoro_unavailable"):
        out.synthesize("Local speech must be available.")


def test_whisper_load_never_downloads(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    calls = []
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(
        WhisperModel=lambda *args, **kwargs: calls.append(kwargs) or object()))
    speech = SpeechToText(Settings(_env_file=None))
    speech._get_model()
    assert calls[0]["local_files_only"] is True
