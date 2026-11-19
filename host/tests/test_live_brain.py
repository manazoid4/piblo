"""Cloud-brain live voice: noise drop, voice commands, brain->TTS->device, fallback, interrupt (fakes only)."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from mazhost import live_brain, live_voice
from mazhost.config import Settings
from mazhost.live_brain import DICTATE_OFF, DICTATE_ON, LiveBrain, is_noise, split_sentences, split_wake
from test_live_voice import REPLY_SAMPLES_24K, TOKEN, FakeRealtime, make_client, speak, text


class FakeBrain:
    """Scripted stand-in for LiveBrain: no network. Class attrs are set per test."""
    text, chunks, hang, stream_closed, dictate = "tell me a joke", [], False, False, False
    asked = threading.Event()

    def __init__(self, cfg, stt=None):
        self.meta, self.on_slow = {"model": "fake/model:free", "fallback": None, "brain_first_token_ms": 5}, None

    async def transcribe(self, pcm):
        assert len(pcm) > 0
        type(self).asked.set()
        return type(self).text

    async def command(self, text, strict=False):
        return None

    def dictating(self):
        return type(self).dictate

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


@pytest.mark.parametrize("said", ["Switch to local model.", "Hey Piblo, switch to the local model.",
                                  "Can you switch to the local model please?", "Use the local model."])
def test_switch_command_survives_natural_phrasing(tmp_path, said):
    brain = brain_for(tmp_path, lambda req: httpx.Response(200, json=MODELS))
    assert asyncio.run(brain.command(said)) == "Okay, I'm using the local model now."


@pytest.mark.parametrize("said", ["What model are you?", "What's your model?", "What AI model are you?",
                                  "Hey Piblo, what model is this?"])
def test_what_model_question_survives_natural_phrasing(tmp_path, said):
    brain = brain_for(tmp_path, lambda req: httpx.Response(200, json=MODELS))
    assert asyncio.run(brain.command(said)).startswith("I'm using ")


def test_general_model_talk_still_reaches_the_brain(tmp_path):
    brain = brain_for(tmp_path, lambda req: httpx.Response(200, json=MODELS))
    assert asyncio.run(brain.command("What's the best model for photos")) is None


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


def test_local_ollama_brain_turns_thinking_off(tmp_path):
    # qwen3.5 on Ollama otherwise spends the whole max_tokens budget reasoning and speaks nothing
    bodies = []

    def handler(req):
        bodies.append((req.url.host, json.loads(req.content)))
        return sse("Hello ", "Maz.")

    brain = brain_for(tmp_path, handler, live_ollama_model="qwen3.5:4b")
    assert asyncio.run(brain.command("switch to local")) == "Okay, I'm using the local model now."

    async def run():
        return "".join([p async for p in brain.stream("who are you")])

    assert asyncio.run(run()) == "Hello Maz."
    host, body = bodies[-1]
    assert host == "127.0.0.1" and body["model"] == "qwen3.5:4b"
    assert body["reasoning_effort"] == "none" and "reasoning" not in body


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


def test_dead_asr_server_is_skipped_on_the_next_turns(tmp_path, monkeypatch):
    monkeypatch.setattr(live_brain, "_asr_down_until", 0.0)
    tries, stt = [], _FakeStt()

    def handler(request):
        tries.append(request)
        return _asr_down(request)

    brain = LiveBrain(Settings(data_dir=str(tmp_path)), transport=httpx.MockTransport(handler), stt=stt)
    for _ in range(3):  # Windows takes ~2.4 s to refuse a dead localhost port: pay it once, not every turn
        assert asyncio.run(brain.transcribe(b"\x00\x01" * 1600)) == "remind me to call mum"
    assert len(tries) == 1 and len(stt.paths) == 3
    monkeypatch.setattr(live_brain, "_asr_down_until", 0.0)  # once the pause is over, LFM3 is tried again
    asyncio.run(brain.transcribe(b"\x00\x01" * 1600))
    assert len(tries) == 2


def test_what_model_reply_is_short_to_speak(tmp_path):
    brain = brain_for(tmp_path, lambda req: httpx.Response(200, json=MODELS))
    reply = asyncio.run(brain.command("what model are you using"))
    assert reply.startswith("I'm using ") and "/" not in reply and ":free" not in reply


def test_asr_server_down_without_local_stt_drops_turn(tmp_path):
    brain = LiveBrain(Settings(data_dir=str(tmp_path)), transport=httpx.MockTransport(_asr_down))
    assert asyncio.run(brain.transcribe(b"\x00\x01" * 1600)) == ""


# ---- dictation mode: "Hey Piblo, <words>" is pasted on the PC ---------------------------------------

@pytest.mark.parametrize("heard,woke,rest", [
    ("Hey Piblo, buy milk.", True, "buy milk."), ("Hey, Pablo. Open my notes", True, "Open my notes"),
    ("hey peeblo send the report", True, "send the report"), ("Piblo, stop", True, "stop"),
    ("Hey Piblo.", True, ""), ("Hey Plu, the quick fox", True, "the quick fox"), ("Hey people", True, ""),
    ("Hey Pebble", True, ""), ("Hey everyone, listen up", False, "Hey everyone, listen up"),
    ("Pick up the kids", False, "Pick up the kids"), ("Hey there, Piblo", False, "Hey there, Piblo")])
def test_split_wake_tolerates_how_speech_to_text_spells_piblo(heard, woke, rest):
    assert split_wake(heard) == (woke, rest)


def test_dictation_mode_is_switched_by_voice_and_persists(tmp_path):
    brain = brain_for(tmp_path, lambda req: httpx.Response(200, json=MODELS))
    assert not brain.dictating()
    assert asyncio.run(brain.command("Hey Piblo, dictation mode.")) == DICTATE_ON
    assert LiveBrain(brain.cfg).dictating()  # survives a new Talk session
    assert asyncio.run(brain.command("switch to talk mode")) == DICTATE_OFF
    assert not LiveBrain(brain.cfg).dictating()
    # dictating "switch to the other branch" must paste, not answer "I don't know a model called..."
    assert asyncio.run(brain.command("switch to the other branch", strict=True)) is None


def dictation_client(monkeypatch, tmp_path, heard):
    fake, pasted = FakeRealtime(), []
    brain = type("B", (FakeBrain,), {"text": heard, "chunks": ["Chat answer. "], "hang": False,
                                     "stream_closed": False, "dictate": True, "asked": threading.Event()})
    monkeypatch.setattr(live_voice, "LiveBrain", brain)
    from mazhost import app as app_mod
    monkeypatch.setattr(app_mod, "flow_foreground", lambda: 7)
    monkeypatch.setattr(app_mod, "flow_safe_paste",
                        lambda text, enter, armed, typer=None: pasted.append((text, enter, armed)) or "pasted")
    return make_client(fake, live_brain="cloud", flow_dir=str(tmp_path / "flow")), brain, pasted


def until_listening(ws):
    """Play out one spoken reply like the device does; returns the states seen."""
    states = []
    while True:
        m = text(ws)
        if isinstance(m, bytes):
            continue
        if m.get("type") == "state":
            states.append(m["state"])
            if m["state"] == "speaking":
                ws.send_text(json.dumps({"type": "playback_done"}))
            elif m["state"] == "listening":
                return states


def test_dictation_pastes_the_words_after_hey_piblo(monkeypatch, tmp_path):
    client, brain, pasted = dictation_client(monkeypatch, tmp_path, "Hey Piblo, um, buy milk and eggs")
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        speak(ws)
        assert until_listening(ws) == ["thinking", "speaking", "listening"]  # Piblo says "Pasted."
        ws.send_text(json.dumps({"type": "bye"}))
    assert pasted == [("Buy milk and eggs.", False, 7)]  # Flow cleanup ran, into the focused window
    assert "dictate intent=paste" in (tmp_path / "flow" / "latency.log").read_text()


def test_dictation_ignores_speech_without_the_wake_phrase(monkeypatch, tmp_path):
    client, brain, pasted = dictation_client(monkeypatch, tmp_path, "and then the TV said something")
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        speak(ws)
        assert brain.asked.wait(3)
        time.sleep(0.3)
        ws.send_text(json.dumps({"type": "bye"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()  # no thinking, no reply: still listening
    assert pasted == []


def test_mode_commands_are_recognised_without_the_wake_phrase():
    from mazhost.live_brain import is_mode_command
    assert is_mode_command("Talk mode.") and is_mode_command("Hey Piblo, dictation mode")
    assert not is_mode_command("the talk mode button is broken")


def test_bare_wake_phrase_makes_the_next_utterance_the_dictation(monkeypatch, tmp_path):
    client, brain, pasted = dictation_client(monkeypatch, tmp_path, "Hey Piblo.")
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        speak(ws)
        until_listening(ws)  # "Yes?"
        assert pasted == []
        brain.text = "Claude, fix the failing test"
        speak(ws)
        until_listening(ws)  # "Sent."
        ws.send_text(json.dumps({"type": "bye"}))
    assert pasted == [("fix the failing test.", True, 7)]  # "Claude ..." pastes and presses Enter
