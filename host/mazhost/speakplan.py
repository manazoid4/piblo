"""Faster spoken replies: first sentence first, rest in parallel, plus turn timings.

The first sentence is synthesised alone (short text = fast Fish call) while the
remainder is synthesised in parallel. The device downloads part 0, starts
playing, and fetches part 1 while it plays. Core starts TTS the moment the LLM
answer exists, so the device never waits for a round trip to begin synthesis.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("uvicorn.error")

_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_ABBREV = ("mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "e.g.", "i.e.", "etc.", "no.")
FIRST_PART_MAX = 110  # cap on part 0 so first audio arrives sooner
SPOKEN_REPLY_MAX = 220  # display keeps full reply; speech stays pocket-sized
SHORT_REPLY_CHARS = 70  # at or below this a single TTS call is already quick


def split_sentences(text: str) -> list[str]:
    """Split on sentence ends; abbreviations and decimals stay together."""
    clean = " ".join(text.split())
    out: list[str] = []
    for piece in _SENT_END.split(clean) if clean else []:
        if out and out[-1].lower().endswith(_ABBREV):
            out[-1] += " " + piece
        else:
            out.append(piece)
    return out


def plan_parts(text: str) -> list[str]:
    """First sentence alone, everything else as one second part."""
    clean = " ".join(text.split())
    if len(clean) <= SHORT_REPLY_CHARS:
        return [clean] if clean else []
    sentences = split_sentences(clean)
    if len(sentences) <= 1:
        return [clean]
    first, rest = sentences[0], " ".join(sentences[1:])
    if len(first) > FIRST_PART_MAX:  # long opener: cut at a clause so part 0 renders fast
        cut = first.rfind(", ", 0, FIRST_PART_MAX)
        if cut >= 30:
            first, rest = first[:cut + 1].rstrip(), (first[cut + 1:].strip() + " " + rest).strip()
    return [first, rest]


def spoken_reply(text: str) -> str:
    """Two concise spoken sentences; the screen always keeps the full answer."""
    clean = " ".join(text.split()).strip()
    clean = re.sub(r"(?m)(?:^|\\s)[*_`#]+(?=\\w)", " ", clean).strip()
    if len(clean) <= SPOKEN_REPLY_MAX:
        return clean
    parts = split_sentences(clean)
    chosen: list[str] = []
    for part in parts[:2]:
        candidate = " ".join([*chosen, part])
        if len(candidate) > SPOKEN_REPLY_MAX:
            break
        chosen.append(part)
    if chosen:
        return " ".join(chosen)
    # Prefer a natural clause/word break when the first sentence is too long.
    cut = clean.rfind(" ", 0, SPOKEN_REPLY_MAX - 3)
    if cut < 50:
        cut = SPOKEN_REPLY_MAX - 3
    return clean[:cut].rstrip(" ,;:") + "..."


def _discard(future: Future) -> None:
    try:
        future.result().unlink(missing_ok=True)
    except Exception:
        pass


class TimingLog:
    """Last N turns of stage timings. No text, no secrets."""

    def __init__(self, size: int = 20) -> None:
        self._rows: deque[dict[str, Any]] = deque(maxlen=size)
        self._lock = threading.Lock()

    def add(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._rows.append(row)
        return row

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._rows]

    @staticmethod
    def line(row: dict[str, Any]) -> str:
        keys = ("upload", "stt", "llm", "tts_first_byte", "tts_total", "first_audio", "parts", "cached")
        return "turn_timing " + " ".join(f"{k}={row.get(k)}" for k in keys if k in row)


class SpeakPlanner:
    def __init__(self, synth: Callable[[str], Path], timings: TimingLog, max_plans: int = 16) -> None:
        self._synth = synth
        self.timings = timings
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tts")
        self._plans: OrderedDict[str, list[Future]] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_plans

    def start(self, text: str, row: dict[str, Any], synth: Callable[[str], Path] | None = None) -> tuple[str, int]:
        parts = plan_parts(spoken_reply(text))
        if not parts:
            return "", 0
        began = time.perf_counter()
        pid = uuid.uuid4().hex[:16]
        remaining = {"n": len(parts)}
        lock = threading.Lock()
        first_done = threading.Event()  # Kokoro's lock isn't FIFO: later parts wait so part 0 renders first

        def run(index: int, part_text: str) -> Path:
            try:
                if index:
                    first_done.wait(40)
                return (synth or self._synth)(part_text)
            finally:
                done_ms = round((time.perf_counter() - began) * 1000)
                with lock:
                    if index == 0:
                        row["tts_first_byte"] = done_ms
                        if "stt" in row and "llm" in row:
                            row["first_audio"] = row.get("stt", 0) + row.get("llm", 0) + done_ms
                    remaining["n"] -= 1
                    if remaining["n"] == 0:
                        row["tts_total"] = done_ms
                        log.info(TimingLog.line(row))

        futures = [self._pool.submit(run, i, p) for i, p in enumerate(parts)]
        futures[0].add_done_callback(lambda _f: first_done.set())  # fires on finish, error or cancel/eviction
        row["parts"] = len(parts)
        with self._lock:
            self._plans[pid] = futures
            while len(self._plans) > self._max:
                _, old = self._plans.popitem(last=False)
                for f in old:
                    if not f.cancel():  # already running or done: remove its temp wav when it lands
                        f.add_done_callback(_discard)
        return pid, len(parts)

    def part(self, pid: str, index: int, timeout: float = 40.0) -> Path:
        with self._lock:
            futures = self._plans.get(pid)
        if futures is None or not 0 <= index < len(futures):
            raise KeyError("unknown_speak_part")
        return futures[index].result(timeout=timeout)  # raises the synth error
