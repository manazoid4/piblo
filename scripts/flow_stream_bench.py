r"""Dictation latency + accuracy bench: streaming STT vs today's Parakeet batch vs local Whisper.

Clips are SYNTHETIC (Windows SAPI TTS), not Maz's voice: they prove the pipeline and relative speed,
not real-mic accuracy. Run with the Core venv python:
  python scripts/flow_stream_bench.py [--dir clips] [--engines stream20m,stream,parakeet,whisper]

Latency = key release -> final text. For streaming, audio is fed at real-time pace (100 ms chunks)
and the clock starts when the last chunk has been handed over, i.e. what is left after release.
For batch engines the clock covers the whole file. VAD mode = trailing-silence wait + the same flush.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

PHRASES = [
    "note buy halal beef for the crisps order",
    "remind me to charge the card puter at nine tonight",
    "open the piblo craft pane for the website",
    "claude run the tests and tell me what failed",
    "switch to the other claude account",
    "start a new job to fix the login bug",
    "what is the status of the pull request",
    "note ring the supplier about the packaging quote",
    "remind me to send the invoice on friday",
    "read the last message from the build agent",
    "pause the current job and show me the board",
    "approve the job on the device",
    "claude summarise what changed in the last commit",
    "open the dashboard for the latest build",
    "note the price of the crisps should be four pounds",
    "remind me to call mum at six",
    "create a branch called feature flow dictation",
    "search the repo for the paste helper",
    "stop the running agent and save the result",
    "say hello to the whole team",
]
WHISPER_MODELS = ("small.en",)


def norm(t: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", t.lower()).split()


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(len(r), 1)


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


def sapi_wav(path: Path, text: str, i: int = 0) -> None:
    ps = ("Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          "$v=@($s.GetInstalledVoices()|?{$_.Enabled}|%{$_.VoiceInfo.Name});"
          f"$s.SelectVoice($v[{i}%$v.Count]);$s.Rate={(-1, 0, 1)[i % 3]};"
          "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
          "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono);"
          f"$s.SetOutputToWaveFile('{path}',$f);$s.Speak('{text}');$s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def make_clips(folder: Path) -> list[tuple[Path, str]]:
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for i, text in enumerate(PHRASES):
        path = folder / f"clip{i:02d}.wav"
        if not path.exists():
            sapi_wav(path, text, i)
        items.append((path, text))
    return items


def pcm_of(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


def run_stream(stt, pcm: bytes, trail_s: float = 0.0):
    """Returns (text, release_ms, vad_ms). Real-time paced feed; clock starts after the last chunk."""
    step = 3200  # 100 ms
    u = stt.stream()
    t_start = time.perf_counter()
    chunks = [pcm[i:i + step] for i in range(0, len(pcm), step)]
    for n, c in enumerate(chunks[:-1]):
        u.feed(c)
        wait = t_start + (n + 1) * 0.1 - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
    t0 = time.perf_counter()
    u.feed(chunks[-1])
    text = u.finish()
    return text, (time.perf_counter() - t0) * 1000


def run_vad(stt, pcm: bytes):
    """Voice-detected end of speech: feed speech then silence until the endpoint fires.
    Latency from the last speech sample = silence wait (audio time) + processing after it."""
    step = 3200
    u = stt.stream()
    silence = b"\x00\x00" * 1600
    for i in range(0, len(pcm), step):
        u.feed(pcm[i:i + step])
    waited = 0.0
    t0 = time.perf_counter()
    while not u.endpoint() and waited < 3.0:
        u.feed(silence)
        waited += 0.1
    proc = (time.perf_counter() - t0) * 1000
    text = u.text()
    return text, waited * 1000 + proc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path.home() / ".maz-pocket" / "flow" / "bench-clips"))
    ap.add_argument("--engines", default="stream,stream20m,parakeet,whisper")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    clips = make_clips(Path(a.dir))
    out: dict = {"synthetic": True, "clips": len(clips), "engines": {}}
    from mazhost import stt_stream

    def report(name, rows, lat=None, vad=None):
        w = [wer(ref, hyp) for ref, hyp in rows]
        e = {"wer": round(sum(w) / len(w), 4)}
        if lat:
            e.update(p50_ms=round(statistics.median(lat)), p95_ms=round(pct(lat, 95)), max_ms=round(max(lat)))
        if vad:
            e.update(vad_p50_ms=round(statistics.median(vad)), vad_p95_ms=round(pct(vad, 95)))
        out["engines"][name] = e
        print(name, e, flush=True)

    for eng in a.engines.split(","):
        if eng in ("stream", "stream20m", "streamnemo"):
            model = {"stream20m": "zipformer-en-20M", "streamnemo": "nemo-fc-80ms"}.get(eng, stt_stream.DEFAULT_MODEL)
            stt = stt_stream.StreamingSTT(model)
            run_stream(stt, pcm_of(clips[0][0]))  # warm
            rows, lat, vad = [], [], []
            for path, ref in clips:
                pcm = pcm_of(path)
                text, ms = run_stream(stt, pcm)
                rows.append((ref, text)); lat.append(ms)
                vad.append(run_vad(stt, pcm)[1])
            report(f"{eng}:{model}", rows, lat, vad)
        elif eng == "parakeet":
            from mazhost.config import Settings
            from mazhost.stt import SpeechToText
            st = SpeechToText(Settings()); st.warm(); st.transcribe_local(clips[0][0])
            rows, lat = [], []
            for path, ref in clips:
                t0 = time.perf_counter(); text, _ = st.transcribe_local(path)
                lat.append((time.perf_counter() - t0) * 1000); rows.append((ref, text))
            report("parakeet-batch(today)", rows, lat)
        elif eng == "whisper":
            from mazhost.config import Settings
            from mazhost.stt import SpeechToText
            st = SpeechToText(Settings())
            for m in WHISPER_MODELS:
                st._transcribe_with(clips[0][0], m, "cpu", "int8")
                rows, lat = [], []
                for path, ref in clips:
                    t0 = time.perf_counter(); text = st._transcribe_with(path, m, "cpu", "int8")
                    lat.append((time.perf_counter() - t0) * 1000); rows.append((ref, text))
                report(f"whisper-{m}-baseline", rows, lat)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
