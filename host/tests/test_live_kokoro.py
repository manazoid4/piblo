"""Kokoro live TTS must deliver PCM before HTTP EOF and close on interruption."""
import asyncio
import time

import httpx
import pytest

from mazhost import live_voice
from mazhost.config import Settings


class Socket:
    def __init__(self):
        self.events = []

    async def send_text(self, text):
        import json
        self.events.append(json.loads(text))


@pytest.mark.parametrize("status", [200, 503])
def test_kokoro_streams_before_eof_and_closes(monkeypatch, status):
    async def run():
        release = asyncio.Event()
        closed = asyncio.Event()
        requests = []

        class PCM(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"\x01" * 1025  # deliberately split a PCM16 sample
                await release.wait()
                yield b"\x01" * 1023

            async def aclose(self):
                closed.set()

        def handler(req):
            requests.append(req)
            return httpx.Response(status, stream=PCM())

        monkeypatch.setattr(live_voice.private_mode, "async_client", lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kw))
        bridge = live_voice.LiveBridge(Socket(), Settings(_env_file=None, tts_provider="kokoro"))
        bridge._t = {"create": time.monotonic()}
        bridge.state = "thinking"

        async def legacy(_):
            raise AssertionError("Kokoro must not open the LFM realtime TTS socket")

        async def play():
            await asyncio.Event().wait()

        monkeypatch.setattr(bridge, "_up_send", legacy)
        monkeypatch.setattr(bridge, "_play", play)
        task = asyncio.create_task(bridge._tts("Here is the opening clause,"))
        try:
            if status != 200:
                with pytest.raises(httpx.HTTPStatusError):
                    await task
            else:
                audio = await asyncio.wait_for(bridge._q.get(), 1)
                assert audio and len(audio) % 2 == 0
                assert not task.done()  # first audio arrived while the server is still producing
                assert bridge.state == "speaking"
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert closed.is_set()
            assert requests[0].url.params["text"] == "Here is the opening clause,"
        finally:
            task.cancel()
            bridge._end_turn_tasks()

    asyncio.run(run())


def test_midstream_failure_stops_playback_and_returns_to_listening(monkeypatch):
    async def run():
        socket = Socket()
        closed = asyncio.Event()

        class PCM(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"\x01" * 2048
                await asyncio.sleep(0.05)
                raise httpx.ReadError("connection lost after first PCM")

            async def aclose(self):
                closed.set()

        monkeypatch.setattr(live_voice.private_mode, "async_client", lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=PCM())), **kw))
        bridge = live_voice.LiveBridge(socket, Settings(_env_file=None, tts_provider="kokoro"))
        bridge._t = {"create": time.monotonic()}

        async def transcribe(_):
            return "give me a short answer"

        async def command(_):
            return "Here is the answer."

        async def play():
            await asyncio.Event().wait()

        monkeypatch.setattr(bridge.brain, "transcribe", transcribe)
        monkeypatch.setattr(bridge.brain, "command", command)
        monkeypatch.setattr(bridge, "_play", play)
        await bridge._cloud_turn(b"\x00\x00")
        assert bridge.state == "listening"
        assert bridge._player is None
        assert {"type": "stop"} in socket.events
        assert closed.is_set()

    asyncio.run(run())


def test_interrupt_during_kokoro_stream_cancels_turn_and_closes_stream(monkeypatch):
    async def run():
        closed = asyncio.Event()

        class PCM(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"\x01" * 2048
                await asyncio.Event().wait()

            async def aclose(self):
                closed.set()

        monkeypatch.setattr(live_voice.private_mode, "async_client", lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=PCM())), **kw))
        socket = Socket()
        bridge = live_voice.LiveBridge(socket, Settings(_env_file=None, tts_provider="kokoro"))
        bridge._t = {"create": time.monotonic()}

        async def transcribe(_):
            return "tell me a story"

        async def command(_):
            return "Once upon a time."

        monkeypatch.setattr(bridge.brain, "transcribe", transcribe)
        monkeypatch.setattr(bridge.brain, "command", command)
        async def play():
            await asyncio.Event().wait()

        monkeypatch.setattr(bridge, "_play", play)
        bridge._cloud = asyncio.create_task(bridge._cloud_turn(b"\x00\x00"))
        for _ in range(200):  # audio is flowing once the player task exists
            if bridge._player:
                break
            await asyncio.sleep(0.01)
        assert not closed.is_set() and bridge.state == "speaking"
        await bridge._control('{"type": "interrupt"}')
        await asyncio.sleep(0.05)
        assert closed.is_set()
        assert bridge._cloud is None and bridge._player is None
        assert bridge.state == "listening"
        assert {"type": "stop"} in socket.events

    asyncio.run(run())
