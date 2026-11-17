"""Cloud-brain live voice: noise drop, voice commands, brain->TTS->device, fallback, interrupt (fakes only)."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from mazhost import live_voice
from mazhost.config import Settings
from mazhost.live_brain import LiveBrain, is_noise, split_sentences
from test_live_voice import REPLY_SAMPLES_24K, TOKEN, FakeRealtime, make_client, speak, text


class FakeBrain:
    """Scripted stand-in for LiveBrain: no network. Class attrs are set per test."""
    text, chunks, hang, stream_closed = "tell me a joke", [], False, False
    asked = threading.Event()

    def __init__(self, cfg, stt=None):
        self.meta, self.on_slow = {"model": "fake/model:free", "fallback": None, "brain_first_token_ms": 5}, None

    async def transcribe(self, pcm):
        assert len(pcm) > 0
        type(self).asked.set()
        return type(self).text

    async def command(self, text):
        return None

    async def stream(self, text):
        self.meta = {"model": "fake/model:free", "fallback": None, "brain_first_token_ms": 5}
        try:
            for c in type(self).chunks:
                yield c
            if type(self).hang:
                await asyncio.sleep(30)
        finally:
            type(self).stream_closed = True


def cloud_client(monkeypatch, fake, **brain_attrs):
    attrs = {"text": "tell me a joke", "chunks": [], "hang": False, "stream_closed": False,
             "asked": threading.Event(), **brain_attrs}
    brain = type("B", (FakeBrain,), attrs)
    monkeypatch.setattr(live_voice, "LiveBrain", brain)
    return make_client(fake, live_brain="cloud"), brain


def test_empty_or_filler_transcript_drops_turn_silently(monkeypatch):
    for heard in ("", ".", "you", "Thank you.", "[music]"):
        fake = FakeRealtime()
        client, brain = cloud_client(monkeypatch, fake, text=heard)
        with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
            assert text(ws)["state"] == "listening"
            speak(ws)
            assert brain.asked.wait(3)
            time.sleep(0.3)
            ws.send_text(json.dumps({"type": "bye"}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()  # no thinking state, no audio: straight back to listening
        assert "response.create" not in fake.events and fake.events.count("session.update") == 1


def test_cloud_answer_is_spoken_sentence_by_sentence(monkeypatch):
    fake = FakeRealtime()
    client, brain = cloud_client(monkeypatch, fake,
                                 chunks=["Why did the chicken cross the road? ", "To get to the", " other side."])
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        speak(ws)
        states, audio = [], b""
        while len(audio) < 2 * REPLY_SAMPLES_24K * 2 * 2 // 3 - 8:  # two sentences x 1 s of fake TTS
            m = text(ws)
            if isinstance(m, bytes):
                audio += m
            elif m["type"] == "state":
                states.append(m["state"])
        assert states == ["thinking", "speaking"]
        ws.send_text(json.dumps({"type": "playback_done"}))
        while True:
            m = text(ws)
            if not isinstance(m, bytes) and m["type"] == "state":
                assert m["state"] == "listening"
                break
        ws.send_text(json.dumps({"type": "bye"}))
    assert fake.events.count("conversation.item.create") == 2 and fake.events.count("response.create") == 2
    assert fake.appended == 0  # speech never went to the speech-to-speech session in cloud mode
    row = client.get("/core/timings", headers={"Authorization": f"Bearer {TOKEN}"}).json()["turns"][-1]
    assert row["model"] == "fake/model:free" and row["asr_ms"] >= 0 and row["first_audio_ms"] >= 0


def test_interrupt_mid_answer_cancels_brain_and_tts(monkeypatch):
    fake = FakeRealtime()
    client, brain = cloud_client(monkeypatch, fake, chunks=["Here is the first sentence of a long answer. "], hang=True)
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        speak(ws)
        while True:
            m = text(ws)
            if not isinstance(m, bytes) and m.get("state") == "speaking":
                break
        ws.send_text(json.dumps({"type": "interrupt"}))
        while True:
            m = text(ws)
            if not isinstance(m, bytes) and m.get("type") == "stop":
                break
        assert text(ws) == {"type": "state", "state": "listening"}
        fake.wait_for("response.cancel")
        end = time.time() + 3
        while not brain.stream_closed and time.time() < end:
            time.sleep(0.02)
        assert brain.stream_closed
        ws.send_text(json.dumps({"type": "bye"}))


def brain_for(tmp_path, handler, **cfg) -> LiveBrain:
    settings = Settings(token=TOKEN, data_dir=str(tmp_path), live_memory_github="", autoupdate=False,
                        remote_detect=False, **cfg)
    return LiveBrain(settings, transport=httpx.MockTransport(handler))


def price(p, c):
    return {"prompt": p, "completion": c}


MODELS = {"data": [
    {"id": "deepseek/deepseek-v4-flash", "context_length": 64000, "pricing": price("0.0000001", "0.0000002")},
    {"id": "deepseek/deepseek-chat:free", "context_length": 128000, "pricing": price("0", "0")},
    {"id": "deepseek/deepseek-r1:free", "context_length": 160000, "pricing": price("0", "0")},
    {"id": "moonshotai/kimi-k2", "context_length": 128000, "pricing": price("0.000001", "0.000002")},
    {"id": "deepseek/deepseek-v4-flash:batch", "context_length": 64000, "pricing": price("0.00000001", "0.00000002")}]}


def test_voice_command_switches_model_and_persists(tmp_path):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json=MODELS)

    brain = brain_for(tmp_path, handler)
    assert asyncio.run(brain.command("Switch to deep seek.")) == "Okay, I'm using DeepSeek now."
    assert LiveBrain(brain.cfg).choice()["model"] == "deepseek/deepseek-chat:free"  # free, non-reasoning, persisted
    assert asyncio.run(brain.command("use kimi")) == "Okay, I'm using Kimi now."
    assert LiveBrain(brain.cfg).choice()["model"] == "moonshotai/kimi-k2"  # no free kimi: cheapest paid
    assert len(calls) == 1  # model list cached for the day
    assert "Kimi" in asyncio.run(brain.command("what model are you using"))
    assert asyncio.run(brain.command("use the bathroom")) is None  # not a model: falls through to the brain
    assert asyncio.run(brain.command("switch to local")) == "Okay, I'm using the local model now."
    assert asyncio.run(brain.command("remember that my dog is called Biscuit")) == "Got it, I'll remember that."
    assert "my dog is called Biscuit" in (tmp_path / "piblo-memory.md").read_text()


def sse(*pieces: str) -> httpx.Response:
    body = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': p}}]})}\n\n" for p in pieces)
    return httpx.Response(200, content=(body + "data: [DONE]\n\n").encode(),
                          headers={"content-type": "text/event-stream"})


def test_openrouter_429_falls_back_to_ollama(tmp_path):
    def handler(req):
        if req.url.host == "openrouter.ai" and req.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        if req.url.host == "openrouter.ai":
            assert req.headers["authorization"] == "Bearer key"
            return httpx.Response(429, json={"error": "rate limited"})
        return sse("Local ", "answer.")

    brain = brain_for(tmp_path, handler, openrouter_api_key="key")
    slow = []
    brain.on_slow = lambda: slow.append(1)

    async def run():
        return "".join([p async for p in brain.stream("hello there")])

    assert asyncio.run(run()) == "Local answer."
    assert brain.meta["fallback"] == "HTTP 429" and brain.meta["model"] == "qwen36-heretic:latest" and slow
    assert brain.history[-1] == {"role": "assistant", "content": "Local answer."}


def test_transcript_noise_filter_and_sentence_cutting():
    assert all(is_noise(t) for t in ("", ".", "you", "Thank you.", "um, yeah", "(silence)"))
    assert not any(is_noise(t) for t in ("hello", "what time is it", "switch to deepseek"))
    done, rest = split_sentences("Hi there. This is **a longer one!** And a part")
    assert done == ["Hi there. This is a longer one!"] and rest == "And a part"
    assert split_sentences("Ok", final=True) == (["Ok"], "")


def test_first_clause_starts_speech_early():
    from mazhost.live_brain import first_clause
    assert first_clause("How about something snappy, like Bleep or Zip") == (["How about something snappy,"], "like Bleep or Zip")
    assert first_clause("Sure, like Bleep") == ([], "Sure, like Bleep")  # too short to be worth a TTS call
    assert first_clause("No comma yet here") == ([], "No comma yet here")


class _FakeStt:
    def __init__(self):
        self.paths = []

    def transcribe_local(self, path):
        self.paths.append(path)
        assert path.read_bytes()[:4] == b"RIFF"  # a real wav reaches the local model
        return " remind me to call mum ", "parakeet"


def _asr_down(request):
    raise httpx.ConnectError("All connection attempts failed", request=request)


def test_asr_server_down_falls_back_to_local_stt(tmp_path):
    stt = _FakeStt()
    brain = LiveBrain(Settings(data_dir=str(tmp_path)), transport=httpx.MockTransport(_asr_down), stt=stt)
    text = asyncio.run(brain.transcribe(b"\x00\x01" * 1600))
    assert text == "remind me to call mum"
    assert len(stt.paths) == 1 and not stt.paths[0].exists()  # temp wav cleaned up


def test_asr_server_down_without_local_stt_drops_turn(tmp_path):
    brain = LiveBrain(Settings(data_dir=str(tmp_path)), transport=httpx.MockTransport(_asr_down))
    assert asyncio.run(brain.transcribe(b"\x00\x01" * 1600)) == ""
