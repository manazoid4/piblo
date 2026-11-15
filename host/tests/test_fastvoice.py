from __future__ import annotations

import time
import wave

import httpx
from mazhost import netpool
import pytest
from fastapi.testclient import TestClient

from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.speakplan import SpeakPlanner, TimingLog, plan_parts, split_sentences, spoken_reply
from mazhost.tts import SpeechOut
from mazhost.voices import ADRIAN_ID

TOKEN = "test-token-that-is-not-default"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 16 + b"data" + b"\x01" * 100


def _settings(tmp_path, **kw):
    return Settings(_env_file=None, token=TOKEN, data_dir=str(tmp_path / "data"),
                    work_dir=str(tmp_path / "work"), control_dir=str(tmp_path / "control"),
                    debug_dir=str(tmp_path / "debug"), fish_api_key="k-secret", tts_enabled=True, **kw)


class Resp:
    def __init__(self, status=200, content=WAV):
        self.status_code, self.content, self.text = status, content, ""


@pytest.fixture
def fish(monkeypatch):
    sent: list[str] = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json["text"])
        return Resp()

    monkeypatch.setattr(netpool.client, "post", post)
    return sent


def test_sentence_splitting_keeps_abbreviations_and_decimals():
    assert split_sentences("Sure. It is 3.5 degrees, said Dr. Who! Really? ok") == [
        "Sure.", "It is 3.5 degrees, said Dr. Who!", "Really? ok"]
    assert split_sentences("  ") == []


def test_plan_parts_first_sentence_alone_and_short_stays_single():
    assert plan_parts("Yes, do it now.") == ["Yes, do it now."]
    long = "Yes, do it now. Then check the logs and report back to me when you are finished with everything."
    assert plan_parts(long) == ["Yes, do it now.", "Then check the logs and report back to me when you are finished with everything."]
    one = "x" * 200
    assert plan_parts(one) == [one]
    assert plan_parts("A. B. C. " + "d" * 80)[1] == "B. C. " + "d" * 80


def test_planner_orders_parts_and_records_timings():
    log = TimingLog(20)

    def synth(text):
        time.sleep(0.15 if text.startswith("Then") else 0.0)  # part 1 slower than part 0
        import tempfile
        from pathlib import Path
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        f.write(text.encode())
        f.close()
        return Path(f.name)

    planner = SpeakPlanner(synth, log)
    row = {"stt": 100, "llm": 200}
    pid, n = planner.start("Do it now. Then check the logs and report back to me when you finish all of it.", row)
    assert n == 2
    p0, p1 = planner.part(pid, 0), planner.part(pid, 1)
    assert p0.read_bytes() == b"Do it now."
    assert p1.read_bytes().startswith(b"Then check")
    time.sleep(0.05)
    assert row["tts_first_byte"] < row["tts_total"]
    assert row["first_audio"] == 300 + row["tts_first_byte"]
    with pytest.raises(KeyError):
        planner.part(pid, 5)
    with pytest.raises(KeyError):
        planner.part("nope", 0)


def test_part_zero_always_renders_before_later_parts():
    done = []

    def synth(text):
        from pathlib import Path
        time.sleep(0.3 if text.startswith("Do it") else 0.0)  # part 0 slow, part 1 instant
        done.append(text[:5])
        return Path("x.wav")

    planner = SpeakPlanner(synth, TimingLog(20))
    pid, n = planner.start("Do it now. Then check the logs and report back to me when you finish all of it.", {})
    assert n == 2
    planner.part(pid, 1)
    planner.part(pid, 0)
    assert done == ["Do it", "Then "]


def test_phrase_cache_serves_second_call_without_fish(tmp_path, fish):
    out = SpeechOut(_settings(tmp_path))
    a = out.synthesize("Sorry, my brain is offline, try again")
    b = out.synthesize("sorry, my brain is offline,  try again")
    assert a.read_bytes() == b.read_bytes()
    assert len(fish) == 1 and out.cache_hits == 1 and out.last_provider == "cache"
    assert (tmp_path / "data" / "tts_cache" / ADRIAN_ID).is_dir()
    # a long, unique reply is never cached
    long = "This is a long one-off answer that must not be cached on disk at all."
    out.synthesize(long)
    out.synthesize(long)
    assert fish.count(long) == 2


def test_phrase_cache_is_per_voice(tmp_path, fish):
    out = SpeechOut(_settings(tmp_path))
    out.synthesize("Done.")
    out.voices.select("933563129e564b19a115bedd57b7406a")
    out.synthesize("Done.")
    assert fish == ["Done.", "Done."]


def _client(tmp_path):
    return TestClient(create_app(_settings(tmp_path)))


def test_turn_returns_timing_ms_speak_parts_and_core_timings(tmp_path, fish):
    api = _client(tmp_path)
    sid = api.post("/session/start", headers=AUTH).json()["session_id"]
    # all real providers are unconfigured: the spoken fallback comes back, which is still a reply
    r = api.post("/turn/text", headers={**AUTH, "X-MAZ-Speak": "1"},
                 json={"session_id": sid, "route": "auto", "text": "hello there"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["timing_ms"]) >= {"upload_ms", "stt_ms", "llm_ms"}
    assert body["speak"]["parts"] >= 1
    part = api.get("/speak", params={"id": body["speak"]["id"], "part": 0}, headers=AUTH)
    assert part.status_code == 200 and part.content[:4] == b"RIFF"
    assert api.get("/speak", params={"id": "bad", "part": 0}, headers=AUTH).status_code == 404
    t = api.get("/core/timings", headers=AUTH).json()["turns"]
    assert t and "llm" in t[-1] and "stt" in t[-1]
    assert api.get("/core/timings").status_code == 401
    assert api.get("/speak", params={"id": "x", "part": 0}).status_code == 401


def test_no_speak_header_means_no_tts_work(tmp_path, fish):
    api = _client(tmp_path)
    sid = api.post("/session/start", headers=AUTH).json()["session_id"]
    body = api.post("/turn/text", headers=AUTH,
                    json={"session_id": sid, "route": "auto", "text": "hello there"}).json()
    assert "speak" not in body and "timing_ms" in body
    assert fish == []


def test_timings_log_is_capped_at_20_and_has_no_text():
    log = TimingLog(20)
    for i in range(30):
        log.add({"stt": i, "llm": i})
    assert len(log.recent()) == 20
    assert "hello" not in TimingLog.line({"stt": 1, "llm": 2, "tts_first_byte": 3})


def test_long_first_sentence_is_capped():
    text = ("Well, that is a really long opening sentence which keeps going and going "
            "without any stop, then it ends right here. Second one.")
    parts = plan_parts(text)
    assert len(parts) == 2 and len(parts[0]) <= 110 and " ".join(parts) == text.replace("stop, then", "stop, then")

def test_short_spoken_reply_keeps_full_screen_text_elsewhere():
    long = ("First, I found the report. Second, I can summarize it now. "
            "Third, I could make you a new report that discusses every single point at length. "
            + "Extra details. " * 30)
    short = spoken_reply(long)
    assert short == "First, I found the report. Second, I can summarize it now."
    assert len(short) <= 220
    assert spoken_reply("Done.") == "Done."
    single = "A " + "quite long explanation " * 40
    assert len(spoken_reply(single)) <= 220 and spoken_reply(single).endswith("...")

def _pcm_wav(path, rate=24000, channels=2):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(2 * channels * (rate // 100)))

def test_tts_device_native_pcm16k_mono(tmp_path):
    path = tmp_path / "stereo.wav"
    _pcm_wav(path)
    SpeechOut._device_wav(path)
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 160

def test_local_kokoro_option_produces_native_wav_without_cloud(tmp_path, monkeypatch):
    out = SpeechOut(_settings(tmp_path, tts_provider="kokoro"))
    calls = []
    def local(text, path):
        calls.append(text)
        _pcm_wav(path)
    monkeypatch.setattr(out, "_kokoro", local)
    first = out.synthesize("Kokoro local example.")
    assert calls == ["Kokoro local example."] and out.last_provider == "kokoro"
    with wave.open(str(first), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
    out.synthesize("Kokoro local example.")
    assert len(calls) == 1 and out.last_provider == "cache"
