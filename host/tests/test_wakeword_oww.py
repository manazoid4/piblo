"""openWakeWord engine: 80 ms framing across odd chunk sizes, one detection per utterance, ungated on failure."""
import sys
import types

import numpy as np

from mazhost import wakeword
from mazhost.config import Settings


def fake_oww(monkeypatch, scores):
    calls = []

    class Model:
        def __init__(self, **kw):
            calls.append(kw)

        def predict(self, frame):
            assert len(frame) == wakeword.OpenWakeWord.FRAME
            return {"m": scores.pop(0) if scores else 0.0}

        def reset(self):
            calls.append("reset")

    monkeypatch.setitem(sys.modules, "openwakeword", types.SimpleNamespace(
        utils=types.SimpleNamespace(download_models=lambda names: calls.append(("dl", names)))))
    monkeypatch.setitem(sys.modules, "openwakeword.utils", sys.modules["openwakeword"].utils)
    monkeypatch.setitem(sys.modules, "openwakeword.model", types.SimpleNamespace(Model=Model))
    return calls


def test_frames_odd_chunks_and_fires_once(monkeypatch):
    calls = fake_oww(monkeypatch, [0.1, 0.9, 0.95])
    ww = wakeword.OpenWakeWord("hey_jarvis", 0.5)
    pcm = np.zeros(1280 * 3, dtype="<i2").tobytes()
    hits = [ww.feed(pcm[i:i + 999 * 2]) for i in range(0, len(pcm), 999 * 2)]
    assert hits.count(True) == 1
    assert ("dl", ["hey_jarvis"]) in calls and "reset" in calls


def test_load_falls_back_ungated_when_engine_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "openwakeword", None)
    monkeypatch.setattr(wakeword, "_warned", False)
    assert wakeword.load(Settings(_env_file=None, wakeword_engine="openwakeword")) is None
