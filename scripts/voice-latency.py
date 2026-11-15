"""Kokoro opening clause vs Kokoro whole reply, TTS stage only, excludes ASR/LLM.

No device commands, playback or configuration changes. Times exclude ASR/LLM/Wi-Fi.
"""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time
import urllib.parse
import urllib.request


async def live_path():
    """Run the patched clause queue, HTTP TTS, resampler and paced socket sender."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
    from mazhost.config import Settings
    from mazhost.live_voice import LiveBridge

    start = time.monotonic()
    row = {"scope": "scripted brain to first 16k socket frame; excludes ASR and hardware"}

    class Socket:
        async def send_text(self, _):
            pass

        async def send_bytes(self, pcm):
            row.setdefault("first_frame_ms", round((time.monotonic() - start) * 1000, 1))
            row["bytes"] = row.get("bytes", 0) + len(pcm)

    class Brain:
        async def stream(self, _):
            for piece in ("Here is the short answer, keep", " the device on Home while I check."):
                yield piece

    bridge = LiveBridge(Socket(), Settings(_env_file=None, tts_provider="kokoro"))
    bridge.brain = Brain()
    bridge.state = "thinking"
    bridge._t = {"create": start}
    queue = asyncio.Queue()
    await bridge._think("synthetic benchmark", queue)
    clauses = 0
    try:
        while (clause := await queue.get()) is not None:
            await bridge._tts(clause)
            clauses += 1
        bridge._q.put_nowait(None)
        bridge._played.set()
        # Avoid the normal turn logger: this harness has no ASR/user turn.
        bridge._log_turn = lambda _: None
        if bridge._player:
            await bridge._player
    finally:
        bridge._end_turn_tasks()
    assert clauses >= 2 and row.get("bytes", 0) > 0, (clauses, row)
    row["clauses"] = clauses
    return row


def measure(text, url):
    start = time.perf_counter()
    req = url + "/?" + urllib.parse.urlencode({"text": text})
    with urllib.request.urlopen(req, timeout=30) as response:
        first = response.read(1024)  # same initial buffer as firmware Kokoro playback
        first_ms = round((time.perf_counter() - start) * 1000, 1)
        size = len(first) + len(response.read())
    assert size >= 1024 and size % 2 == 0, "not usable PCM16"
    return {"first_pcm_ms": first_ms, "total_ms": round((time.perf_counter() - start) * 1000, 1),
            "bytes": size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--live-path", action="store_true", help="also exercise the patched Live queue")
    args = parser.parse_args()
    # Synthetic text only: no conversation or customer data in timing artifacts.
    clause = "Here is the short answer,"
    reply = clause + " you can keep the device on Home while the computer prepares the rest of the reply."
    result = {"scope": "Kokoro opening clause vs Kokoro whole reply, TTS stage only (first 1024 PCM bytes); excludes ASR/LLM/device playback",
              "core_health": urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=3).status,
              "whole": [], "clause": []}
    for _ in range(args.runs):
        for name, text in (("whole", reply), ("clause", clause)):
            result[name].append(measure(text, "http://127.0.0.1:8880"))
    for name in ("whole", "clause"):
        result[name + "_median_ms"] = statistics.median(r["first_pcm_ms"] for r in result[name])
    if args.live_path:
        result["live_path"] = asyncio.run(live_path())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
