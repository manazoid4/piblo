"""Piblo Flow accuracy bench: spoken phrase -> Core /dictate -> did the right text and intent come out?

Clips are SYNTHETIC (Windows SAPI voices), not a person or the device mic. They test the whole
Core path (real STT engine, cleanup, dictionary, prefix routing) and catch regressions; real-mic
accuracy still needs a person.

Corpus: JSON lines {"say": spoken words, "intent": paste|note|remind|claude, "text": expected text}.

  python scripts/flow_accuracy_bench.py --core http://127.0.0.1:8797 --token T --corpus scripts/flow_corpus.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from flow_stream_bench import norm, pct, sapi_wav, wer  # noqa: E402


def make_local(engine: str):
    """wav -> (Flow result, ms). ms = after-release time: stream is fed in real time first, like holding the key."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
    import os
    os.environ.setdefault("PIBLO_LEDGER_DIR", tempfile.mkdtemp())  # keep bench text out of the real ledger
    from mazhost.config import Settings
    from mazhost.flow import Flow

    flow = Flow(Settings(token="x" * 40, flow_dir=tempfile.mkdtemp()), speech=None)
    if engine == "stream":
        from flow_stream_bench import pcm_of
        from mazhost.stt_stream import StreamingSTT
        stt = StreamingSTT()

        def run(wav: Path):
            utt, pcm = stt.stream(), pcm_of(wav)
            for k in range(0, len(pcm), 3200):  # 100 ms chunks, decoded while "held"
                utt.feed(pcm[k:k + 3200])
            t = time.perf_counter()
            res = flow.finish(utt.finish(), "stream")
            return res, round((time.perf_counter() - t) * 1000)
        return run
    from mazhost.stt import SpeechToText
    stt = SpeechToText(Settings(token="x" * 40, stt_provider="parakeet"))

    def run(wav: Path):
        t = time.perf_counter()
        res = flow.finish(stt._parakeet(wav), "parakeet")
        return res, round((time.perf_counter() - t) * 1000)
    return run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default="http://127.0.0.1:8787")
    ap.add_argument("--token", default="")
    ap.add_argument("--corpus", default=str(Path(__file__).with_name("flow_corpus.jsonl")))
    ap.add_argument("--clips", default=str(Path(tempfile.gettempdir()) / "piblo-flow-bench"))
    ap.add_argument("--json", default="")
    ap.add_argument("--local", choices=["stream", "parakeet"], default="",
                    help="skip Core: run this STT engine + Flow cleanup in-process (Core venv python)")
    a = ap.parse_args()
    local = make_local(a.local) if a.local else None

    cases = [json.loads(line) for line in Path(a.corpus).read_text(encoding="utf-8").splitlines() if line.strip()]
    clips = Path(a.clips)
    clips.mkdir(parents=True, exist_ok=True)
    rows, client = [], httpx.Client(timeout=120)
    for i, case in enumerate(cases):
        wav = clips / f"{i:03d}.wav"
        if not wav.exists():
            sapi_wav(wav, case["say"].replace("'", "''"), i)  # quoted inside a PowerShell '...' string
        if local:
            j, ms = local(wav)
        else:
            t = time.perf_counter()
            r = client.post(f"{a.core}/dictate?target=text", content=wav.read_bytes(),
                            headers={"Authorization": f"Bearer {a.token}", "Content-Type": "audio/wav"})
            ms = round((time.perf_counter() - t) * 1000)
            r.raise_for_status()
            j = r.json()
        row = {"i": i, "say": case["say"], "want_intent": case["intent"], "got_intent": j["intent"],
               "want": case["text"], "got": j["text"], "raw": j.get("raw", ""), "ms": ms,
               "wer": round(wer(case["text"], j["text"]), 3),
               "exact": j["text"].strip() == case["text"].strip()}
        rows.append(row)
        flag = "OK " if row["got_intent"] == row["want_intent"] and row["wer"] == 0 else "BAD"
        print(f"{flag} {i:02d} {ms:4d}ms wer={row['wer']:.2f} [{row['got_intent']}/{row['want_intent']}] {j['text']!r}")
        if flag == "BAD":
            print(f"        want {case['text']!r}\n        raw  {row['raw']!r}")

    n = len(rows)
    intent_ok = sum(r["got_intent"] == r["want_intent"] for r in rows)
    words_ok = sum(r["wer"] == 0 for r in rows)
    ref_words = sum(len(norm(r["want"])) for r in rows)
    mean_wer = sum(r["wer"] * len(norm(r["want"])) for r in rows) / max(ref_words, 1)
    lat = [r["ms"] for r in rows]
    summary = {"n": n, "intent_ok": intent_ok, "words_exact": words_ok, "exact_text": sum(r["exact"] for r in rows),
               "wer": round(mean_wer, 4), "p50_ms": pct(lat, 50), "p95_ms": pct(lat, 95)}
    print(json.dumps(summary))
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
