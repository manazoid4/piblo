#!/usr/bin/env python3
"""Piblo voice bench: live Core /speak (TTS) -> /transcribe/raw (STT) round trip.

Measures time-to-first-byte and total for synthesis, then WER of the STT transcript
vs the phrase. Read/synthesis endpoints only; never restarts or reconfigures Core.
Usage: python scripts/voice_bench.py [--core http://127.0.0.1:8787]
Token: MAZ_TOKEN from %LOCALAPPDATA%\\Piblo\\Core\\.env (never printed).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import date
from pathlib import Path

import httpx

PHRASES = {
    "short": "Okay, done.",
    "long": ("Your meeting with the dentist is at ten thirty tomorrow morning, so you should leave "
             "the house by ten, and I have set a reminder for nine forty five just in case."),
    "name": "Hi, I am Piblo, your pocket assistant on the Cardputer.",
    "numbers": "The total is 42 pounds and your code is 7 1 9 3.",
}
ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def words(text: str) -> list[str]:
    out: list[str] = []
    for tok in re.findall(r"[a-z0-9']+", text.lower().replace("-", " ")):
        if tok.isdigit() and int(tok) < 100:  # shortcut: numbers >= 100 compare as-is
            n = int(tok)
            out += [ONES[n]] if n < 20 else [TENS[n // 10]] + ([ONES[n % 10]] if n % 10 else [])
        else:
            out.append(tok)
    return out


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[-1] / max(len(r), 1)


def token() -> str:
    env = Path(os.environ["LOCALAPPDATA"]) / "Piblo" / "Core" / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("MAZ_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("MAZ_TOKEN not found")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default="http://127.0.0.1:8787")
    ap.add_argument("--out", default="docs/evidence")
    a = ap.parse_args()
    with httpx.Client(base_url=a.core, headers={"Authorization": f"Bearer {token()}"}, timeout=120) as c:
        voices = c.get("/voices").json()  # Fish ids; /speak ignores them when the live provider is Kokoro
        rows = []
        for name, text in PHRASES.items():
            t0 = time.perf_counter()
            with c.stream("POST", "/speak", json={"text": text}) as r:
                r.raise_for_status()
                ttfb, chunks = None, []
                for chunk in r.iter_bytes():
                    ttfb = ttfb if ttfb is not None else time.perf_counter() - t0
                    chunks.append(chunk)
                total = time.perf_counter() - t0
                provider = r.headers.get("x-tts-provider", "?")
            wav = b"".join(chunks)
            s0 = time.perf_counter()
            res = c.post("/transcribe/raw", content=wav, headers={"Content-Type": "audio/wav"})
            res.raise_for_status()
            hyp = res.json().get("transcript", "")
            rows.append({"phrase": name, "text": text, "provider": provider, "ttfb_ms": round(ttfb * 1000),
                         "total_ms": round(total * 1000), "wav_bytes": len(wav),
                         "stt_ms": round((time.perf_counter() - s0) * 1000),
                         "transcript": hyp, "wer": round(wer(text, hyp), 3)})
    day = date.today().isoformat()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"voice-bench-{day}.json").write_text(json.dumps(
        {"date": day, "voices_listed": [v["name"] for v in voices.get("voices", [])], "rows": rows},
        indent=2), encoding="utf-8")
    md = [f"# Voice bench {day}", "",
          "Live Core `/speak` -> `/transcribe/raw`. One pass per phrase; provider `cache` means a cached "
          "phrase, not a cold render. Per-voice comparison is not possible: `/speak` uses the configured "
          "provider's single voice and `/voices` lists Fish ids only.", "",
          "| phrase | provider | first byte ms | total ms | STT ms | WER | transcript |",
          "|---|---|---|---|---|---|---|"]
    md += [f"| {r['phrase']} | {r['provider']} | {r['ttfb_ms']} | {r['total_ms']} | {r['stt_ms']} "
           f"| {r['wer']:.2f} | {r['transcript']} |" for r in rows]
    (out / f"voice-bench-{day}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
