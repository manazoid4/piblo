"""Streaming push-to-talk: release -> text path, latency log and stats (model-free except the last test)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nodflow  # noqa: E402
from mazhost import stt_stream  # noqa: E402
from mazhost.config import Settings  # noqa: E402
from mazhost.flow import Flow  # noqa: E402


class FakeHold:
    def __init__(self, text): self.text = text
    def release(self): return self.text


def _flow(tmp_path):
    return Flow(Settings(flow_dir=str(tmp_path)), None)


def test_release_pastes_cleaned_text_and_logs_ptt_line(tmp_path):
    flow, typed = _flow(tmp_path), []
    def paste_fn(t, enter=False, on_typed=None):
        typed.append((t, enter)); on_typed()
    r = nodflow.finish_hold(FakeHold("NOTE" if False else "REMIND ME TO CHARGE THE CARD PEWTER"), flow, time.perf_counter(),
                            paste_fn, hwnd=7, safe_fn=lambda text, enter, hwnd, fn: fn(text, enter) or "pasted")
    assert r["intent"] == "remind" and typed == []  # reminders are saved, never pasted
    r = nodflow.finish_hold(FakeHold("OPEN THE PIBLO FLOW PANE"), flow, time.perf_counter(), paste_fn, hwnd=7,
                            safe_fn=lambda text, enter, hwnd, fn: fn(text, enter) or "pasted")
    assert typed == [("Open the Piblo Flow pane.", False)]
    line = (tmp_path / "latency.log").read_text().splitlines()[-1]
    assert " ptt mode=ptt release_to_text=" in line and "pc=pasted" in line


def test_claude_prefix_presses_enter(tmp_path):
    seen = []
    nodflow.finish_hold(FakeHold("CLAUDE RUN THE TESTS"), _flow(tmp_path), time.perf_counter(),
                        lambda t, enter=False, on_typed=None: seen.append(enter),
                        safe_fn=lambda text, enter, hwnd, fn: fn(text, enter) or "pasted")
    assert seen == [True]


def test_silence_pastes_nothing(tmp_path):
    r = nodflow.finish_hold(FakeHold("  "), _flow(tmp_path), time.perf_counter(), None)
    assert r["text"] == ""


def test_stats_splits_modes(tmp_path):
    log = tmp_path / "latency.log"
    log.write_text("".join(f"t ptt mode={m} release_to_text={v}\n" for m, v in
                           [("ptt", 60), ("ptt", 80), ("ptt", 100), ("vad", 700)]) + "t dictate total=500\n")
    out = nodflow.stats(log)
    assert out["ptt"] == {"n": 3, "p50_ms": 80, "p95_ms": 100} and out["vad"]["n"] == 1


@pytest.mark.skipif(not stt_stream.available(), reason="streaming model not installed (scripts/setup-stream-stt.py)")
def test_real_model_transcribes_a_synthetic_clip(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import flow_stream_bench as bench
    clip = tmp_path / "c.wav"
    bench.sapi_wav(clip, "approve the job on the device")  # synthetic SAPI voice, not a person
    pcm = bench.pcm_of(clip)
    hold = nodflow.Hold(stt_stream.StreamingSTT())
    for i in range(0, len(pcm), 3200):
        hold.feed(pcm[i:i + 3200])
    text = hold.release()
    assert "DEVICE" in text  # speed is measured by scripts/flow_stream_bench.py, not asserted (CI load varies)
