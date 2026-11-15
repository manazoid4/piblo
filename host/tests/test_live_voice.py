from __future__ import annotations

import asyncio
import base64
import json
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.live_voice import Resampler24to16, boost

TOKEN = "t" * 32
REPLY_SAMPLES_24K = 24000  # 1 s of reply


class FakeRealtime:
    """Stand-in for llama-server /v1/realtime: records client events, answers response.create."""

    def __init__(self, reply: bool = True) -> None:
        self.reply, self.events, self.appended = reply, [], 0
        started = threading.Event()
        threading.Thread(target=self._run, args=(started,), daemon=True).start()
        started.wait(5)

    def _run(self, started: threading.Event) -> None:
        async def main():
            async with serve(self._handle, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                started.set()
                await asyncio.Future()

        asyncio.run(main())

    async def _handle(self, ws) -> None:
        await ws.send(json.dumps({"type": "session.created"}))
        async for raw in ws:
            ev = json.loads(raw)
            self.events.append(ev["type"])
            if ev["type"] == "session.update":
                await ws.send(json.dumps({"type": "session.updated"}))
            elif ev["type"] == "input_audio_buffer.append":
                self.appended += len(base64.b64decode(ev["audio"]))
            elif ev["type"] == "response.create" and self.reply:
                await ws.send(json.dumps({"type": "response.output_text.delta", "delta": "Hi"}))
                pcm = (np.sin(np.arange(REPLY_SAMPLES_24K) / 5) * 6000).astype("<i2").tobytes()
                for i in range(0, len(pcm), 9600):  # 4x-realtime style bursts
                    await ws.send(json.dumps({"type": "response.output_audio.delta",
                                              "delta": base64.b64encode(pcm[i:i + 9600]).decode()}))
                await ws.send(json.dumps({"type": "response.done"}))

    def wait_for(self, name: str, timeout: float = 3) -> None:
        end = time.time() + timeout
        while name not in self.events and time.time() < end:
            time.sleep(0.02)
        assert name in self.events, self.events


def make_client(fake: FakeRealtime, gain: float = 1.0, **extra) -> TestClient:
    extra.setdefault("live_brain", "lfm")  # these tests cover direct speech-to-speech; cloud is in test_live_brain.py
    cfg = Settings(token=TOKEN, llamacpp_url=f"http://127.0.0.1:{fake.port}", live_end_silence_ms=200, live_mic_gain=gain, live_listen_holdoff_ms=0,
                   autoupdate=False, remote_detect=False, **extra)
    return TestClient(create_app(cfg))


def tone(ms: int, amp: int = 8000) -> list[bytes]:
    n = 640 * ms // 40  # samples per 40 ms frame = 640
    x = (np.sin(np.arange(n) / 4) * amp).astype("<i2").tobytes()
    return [x[i:i + 1280] for i in range(0, len(x), 1280)]


def quiet(ms: int) -> list[bytes]:
    return [bytes(1280)] * (ms // 40)


def hiss(ms: int) -> list[bytes]:
    rng = np.random.default_rng(1)
    return [(rng.normal(0, 100, 640)).astype("<i2").tobytes() for _ in range(ms // 40)]


def text(ws):
    msg = ws.receive()
    return json.loads(msg["text"]) if msg.get("text") else msg["bytes"]


def speak(ws) -> None:
    for f in tone(600) + quiet(400):
        ws.send_bytes(f)


def test_rejects_missing_or_wrong_token():
    client = make_client(FakeRealtime())
    for url, headers in (("/voice/live", None), ("/voice/live?token=nope", None),
                         ("/voice/live", {"Authorization": "Bearer nope"})):
        with client.websocket_connect(url, headers=headers or {}) as ws:
            with pytest.raises(WebSocketDisconnect) as err:
                ws.receive_text()
            assert err.value.code == 4401


def test_accepts_header_and_query_token():
    client = make_client(FakeRealtime())
    for url, headers in (("/voice/live", {"Authorization": f"Bearer {TOKEN}"}), (f"/voice/live?token={TOKEN}", None)):
        with client.websocket_connect(url, headers=headers or {}) as ws:
            assert text(ws) == {"type": "state", "state": "listening"}


def test_full_turn_audio_is_resampled_and_states_flow():
    fake = FakeRealtime()
    client = make_client(fake)
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        ws.send_text(json.dumps({"type": "hello", "fw": "t"}))
        speak(ws)
        states, audio = [], b""
        while len(audio) < 31000:
            m = text(ws)
            if isinstance(m, bytes):
                assert len(m) <= 1280
                audio += m
            elif m["type"] == "state":
                states.append(m["state"])
        assert states == ["thinking", "speaking"]
        assert abs(len(audio) - REPLY_SAMPLES_24K * 2 * 2 // 3) <= 4  # 24k -> 16k = 2/3 of the samples
        ws.send_text(json.dumps({"type": "playback_done"}))
        while True:
            m = text(ws)
            if not isinstance(m, bytes) and m["type"] == "state":
                assert m["state"] == "listening"
                break
        ws.send_text(json.dumps({"type": "bye"}))
    assert fake.events[:2] == ["session.update", "input_audio_buffer.append"]
    assert fake.events.count("response.create") == 1
    assert 0 < fake.appended < 1000 * 32  # speech + pre-roll + hangover only, bounded
    row = client.get("/core/timings", headers={"Authorization": f"Bearer {TOKEN}"}).json()["turns"][-1]
    assert row["live"] and row["speech_ms"] >= 250 and row["first_audio_ms"] >= 0 and row["audio_s"] > 0.9


def test_interrupt_sends_stop_and_cancels():
    fake = FakeRealtime(reply=False)  # model never answers: we are "thinking"
    client = make_client(fake)
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        speak(ws)
        assert text(ws) == {"type": "state", "state": "thinking"}
        fake.wait_for("response.create")
        ws.send_text(json.dumps({"type": "interrupt"}))
        assert text(ws) == {"type": "stop"}
        assert text(ws) == {"type": "state", "state": "listening"}
        fake.wait_for("response.cancel")
        ws.send_text(json.dumps({"type": "bye"}))


def test_vad_ignores_low_level_noise():
    fake = FakeRealtime()
    client = make_client(fake)
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        for f in hiss(3000):
            ws.send_bytes(f)
        ws.send_text(json.dumps({"type": "bye"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()  # nothing but the initial state was ever sent
    assert fake.appended == 0 and "response.create" not in fake.events


def test_resampler_streaming_matches_one_shot_length():
    pcm = (np.arange(24000) % 200).astype("<i2").tobytes()
    r = Resampler24to16()
    out = b"".join(r.feed(pcm[i:i + 1000]) for i in range(0, len(pcm), 1000))
    assert abs(len(out) - 32000) <= 4


def _tone_rms_through_resampler(hz: float) -> float:
    t = np.arange(24000) / 24000
    pcm = (8000 * np.sin(2 * np.pi * hz * t)).astype("<i2").tobytes()
    r = Resampler24to16()
    out = np.frombuffer(b"".join(r.feed(pcm[i:i + 960]) for i in range(0, len(pcm), 960)), "<i2")
    return float(np.sqrt(np.mean(out[4000:].astype(np.float64) ** 2)))  # skip filter warm-up


def test_resampler_keeps_speech_band():
    assert 5000 < _tone_rms_through_resampler(1000) < 6200  # 8000 peak sine = 5657 rms


def test_resampler_blocks_aliasing_and_rattle():
    # 10 kHz would alias to 6 kHz grit at 16 kHz; 80 Hz only rattles the speaker.
    assert _tone_rms_through_resampler(10000) < 60
    assert _tone_rms_through_resampler(80) < 120


def test_boost_scales_and_clips():
    x = np.array([100, -100, 4000, -4000], dtype="<i2").tobytes()
    y = np.frombuffer(boost(x, 10.0), "<i2")
    assert list(y) == [1000, -1000, 32767, -32768]


def test_quiet_cardputer_speech_starts_a_turn_with_gain():
    """Real ADV levels: speech rms ~200, room ~20. Without gain this never crossed the VAD."""
    fake = FakeRealtime()
    client = make_client(fake, gain=10.0)
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        rng = np.random.default_rng(2)
        room = [(rng.normal(0, 20, 640)).astype("<i2").tobytes() for _ in range(25)]
        for f in room + tone(800, amp=280) + quiet(400):
            ws.send_bytes(f)
        assert text(ws)["state"] == "thinking"
        ws.send_text(json.dumps({"type": "bye"}))
    assert fake.appended > 0 and "response.create" in fake.events


def test_boosted_room_noise_never_starts_a_turn():
    """Measured ADV room noise (rms ~40) boosted 10x must not trigger the 'I'm here to help' loop."""
    fake = FakeRealtime()
    client = make_client(fake, gain=10.0)
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["state"] == "listening"
        rng = np.random.default_rng(3)
        for _ in range(100):
            ws.send_bytes((rng.normal(0, 40, 640)).astype("<i2").tobytes())
        ws.send_text(json.dumps({"type": "bye"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert "response.create" not in fake.events


def _vad_ended_after(loud_ms, silence_ms):
    import numpy as np
    from mazhost.live_voice import Vad, BLOCK_MS
    v = Vad(450)
    loud = (np.sin(np.arange(320) / 3) * 5000).astype("<i2").tobytes()
    quiet = bytes(640)
    for _ in range(loud_ms // BLOCK_MS):
        v.feed(loud)
    for i in range(silence_ms // BLOCK_MS):
        if v.feed(quiet)[1]:
            return (i + 1) * BLOCK_MS
    return None


def test_short_utterance_ends_after_base_silence():
    assert _vad_ended_after(600, 1500) == 440  # 22 blocks, within one block of 450 ms


def test_long_utterance_waits_longer_for_mid_thought_pause():
    assert _vad_ended_after(2400, 1500) == 660  # 22 blocks * 1.5 = 33 blocks


def test_long_speech_boundary_is_1500_ms_of_speech():
    assert _vad_ended_after(1500, 1500) == 660
    assert _vad_ended_after(1480, 1500) == 440


class BadUpstream:
    """Realtime upstream that fails the handshake: model_not_found error, hangs, or closes at once."""

    def __init__(self, mode: str) -> None:
        self.mode, self.paths = mode, []
        started = threading.Event()
        threading.Thread(target=self._run, args=(started,), daemon=True).start()
        started.wait(5)

    def _run(self, started: threading.Event) -> None:
        async def main():
            async with serve(self._handle, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                started.set()
                await asyncio.Future()

        asyncio.run(main())

    async def _handle(self, ws) -> None:
        self.paths.append(ws.request.path)
        if self.mode == "model_not_found":  # llama-swap: text-only server, no session.created
            await ws.send(json.dumps({"type": "error", "error": {"code": "model_not_found"}}))
            await asyncio.Future()
        elif self.mode == "hang":
            await asyncio.Future()
        # "close": handler returns, socket closes before any event


@pytest.mark.parametrize("mode", ["model_not_found", "hang", "close"])
def test_upstream_handshake_failure_sends_error_frame_and_ends_cleanly(mode):
    upstream = BadUpstream(mode)
    client = make_client(upstream)
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws) == {"type": "error", "message": "Piblo's brain is offline"}
        with pytest.raises(WebSocketDisconnect) as err:
            ws.receive_text()
        assert err.value.code == 1011


def test_live_model_setting_is_sent_upstream():
    upstream = BadUpstream("close")
    client = make_client(upstream, live_model="lfm-omni")
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["type"] == "error"
    assert upstream.paths == ["/v1/realtime?model=lfm-omni"]


def test_cloud_brain_never_transcribes_when_upstream_fails(monkeypatch):
    from mazhost.live_brain import LiveBrain

    calls = []

    async def no_asr(self, pcm):
        calls.append(len(pcm))
        return "hello"

    monkeypatch.setattr(LiveBrain, "transcribe", no_asr)
    client = make_client(BadUpstream("model_not_found"), live_brain="cloud")
    with client.websocket_connect(f"/voice/live?token={TOKEN}") as ws:
        assert text(ws)["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert calls == []
