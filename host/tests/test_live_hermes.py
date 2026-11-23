"""Piblo Talk (Live voice) hands turns to Hermes when the owner chose Hermes."""
import asyncio
import time

import pytest

from mazhost import live_voice
from mazhost.config import Settings


class Socket:
    def __init__(self):
        self.events = []

    async def send_text(self, text):
        import json
        self.events.append(json.loads(text))


class FakeHermes:
    def __init__(self, selected=True):
        self._selected, self.asked, self.models = selected, [], []

    def selected(self):
        return self._selected

    def model_command(self, text):
        if text.startswith("switch to"):
            self.models.append(text)
            return {"reply": "Model switched to deepseek."}
        return None

    def answer(self, text):
        self.asked.append(text)
        return {"reply": "First sentence from Hermes. And a second one here."}


def run_turn(monkeypatch, heard, hermes, brain_command=None):
    spoken = []

    async def go():
        bridge = live_voice.LiveBridge(Socket(), Settings(_env_file=None, tts_provider="kokoro"), hermes=hermes)
        bridge._t = {"create": time.monotonic()}

        async def transcribe(_):
            return heard

        async def command(text):
            return brain_command

        async def stream(text):
            raise AssertionError("Piblo's brain must not answer when Hermes is chosen")
            yield ""

        async def tts(sentence):
            spoken.append(sentence)

        monkeypatch.setattr(bridge.brain, "transcribe", transcribe)
        monkeypatch.setattr(bridge.brain, "command", command)
        if hermes and hermes.selected():
            monkeypatch.setattr(bridge.brain, "stream", stream)
        monkeypatch.setattr(bridge, "_tts", tts)
        await bridge._cloud_turn(b"\x00\x00")
        return bridge

    bridge = asyncio.run(go())
    return spoken, bridge


def test_talk_turn_goes_to_hermes_sentence_by_sentence(monkeypatch):
    hermes = FakeHermes()
    spoken, bridge = run_turn(monkeypatch, "what is on my calendar", hermes)
    assert hermes.asked == ["what is on my calendar"]
    assert spoken == ["First sentence from Hermes.", "And a second one here."]
    assert {"type": "text", "role": "user", "text": "what is on my calendar"} in bridge.ws.events


def test_talk_model_switch_goes_to_hermes_model(monkeypatch):
    hermes = FakeHermes()
    spoken, _ = run_turn(monkeypatch, "switch to deepseek", hermes)
    assert hermes.models == ["switch to deepseek"] and hermes.asked == [] and spoken == ["Model switched to deepseek."]


def test_talk_local_command_still_answers_first(monkeypatch):
    hermes = FakeHermes()
    spoken, _ = run_turn(monkeypatch, "remember the bins", hermes, brain_command="Got it, I'll remember that.")
    assert hermes.asked == [] and spoken == ["Got it, I'll remember that."]


def test_talk_uses_piblo_brain_when_hermes_not_chosen(monkeypatch):
    hermes = FakeHermes(selected=False)
    spoken, _ = run_turn(monkeypatch, "hello there", hermes, brain_command="Hi from Piblo.")
    assert hermes.asked == [] and spoken == ["Hi from Piblo."]


def test_interrupt_cancels_a_running_hermes_turn(monkeypatch):
    cancelled = []

    class Link:
        def status(self):
            return {"busy": True}

        def cancel(self):
            cancelled.append(True)

    hermes = FakeHermes()
    hermes.link = Link()

    async def go():
        bridge = live_voice.LiveBridge(Socket(), Settings(_env_file=None, tts_provider="kokoro"), hermes=hermes)
        bridge.state = "speaking"
        await bridge._interrupt()
        for _ in range(50):
            if cancelled:
                break
            await asyncio.sleep(0.01)
        return bridge

    bridge = asyncio.run(go())
    assert cancelled == [True] and bridge.state == "listening"
