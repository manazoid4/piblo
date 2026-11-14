import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np

from mazhost.live_voice import LiveBridge


class FakeWakeword:
    def __init__(self, path):
        self.match = False
        self.samples = []
        self.resets = 0

    def feed(self, pcm):
        self.samples.append(pcm)
        return self.match

    def reset(self):
        self.resets += 1
        self.match = False


def test_wake_gate_and_rearm(monkeypatch):
    monkeypatch.setattr('mazhost.wakeword.Wakeword', FakeWakeword)
    cfg = SimpleNamespace(wakeword_model_dir='model', llamacpp_url='http://localhost',
                          live_end_silence_ms=200, live_mic_gain=2,
                          live_listen_holdoff_ms=0, live_brain='lfm')
    bridge = LiveBridge(None, cfg)
    bridge._up_send = AsyncMock()
    bridge._send = AsyncMock()
    block = np.full(320, 1000, dtype='<i2').tobytes()

    async def run():
        await bridge._mic(block * 20)
        assert bridge._up_send.await_count == 0
        assert np.frombuffer(bridge.wakeword.samples[0], '<i2')[0] == 1000
        bridge.wakeword.match = True
        await bridge._mic(block)
        assert bridge._up_send.await_count == 0
        await bridge._mic(block * 20)
        assert bridge._up_send.await_count == 1
        await bridge._set_state('speaking')
        await bridge._set_state('listening')
        assert bridge._armed and bridge.wakeword.resets == 1
        await bridge._mic(block * 20)
        assert bridge._up_send.await_count == 1

    asyncio.run(run())


def test_cloud_handler_precedes_brain_command():
    cfg = SimpleNamespace(wakeword_model_dir='', llamacpp_url='http://localhost',
                          live_end_silence_ms=200, live_mic_gain=1,
                          live_listen_holdoff_ms=0, live_brain='lfm')
    handler = AsyncMock(return_value='Done')
    bridge = LiveBridge(None, cfg, command_handler=handler)
    bridge.brain = SimpleNamespace(meta={}, transcribe=AsyncMock(return_value='open notes'),
                                   command=AsyncMock())
    bridge._send = AsyncMock()
    bridge._tts = AsyncMock()
    bridge._t = {}
    asyncio.run(bridge._cloud_turn(b'audio'))
    handler.assert_awaited_once_with('open notes')
    bridge.brain.command.assert_not_awaited()
    bridge._tts.assert_awaited_once_with('Done')


def test_missing_model_falls_back_to_ungated(tmp_path, monkeypatch):
    import mazhost.wakeword as wakeword
    monkeypatch.setattr(wakeword, '_warned', False)
    warnings = []
    monkeypatch.setattr(wakeword.log, 'warning', lambda *a: warnings.append(a))
    cfg = SimpleNamespace(wakeword_model_dir=str(tmp_path), llamacpp_url='http://localhost',
                          live_end_silence_ms=200, live_mic_gain=1,
                          live_listen_holdoff_ms=0, live_brain='lfm')
    first, second = LiveBridge(None, cfg), LiveBridge(None, cfg)
    assert first.wakeword is None and not first._armed and second.wakeword is None
    assert len(warnings) == 1 and 'incomplete' in str(warnings[0][-1])  # logged once
    cfg.wakeword = False
    assert wakeword.load(cfg) is None
