"""Real-microphone Flow test that never types into a window (safe while nobody is at the PC).

Plays a spoken phrase from the PC speakers into the Cardputer's real mic, drives Type on PC with
two SPACE taps through Core, and checks the result arrives on the PC clipboard. Aborts unless the
focused window is one Core cannot type into (an elevated window), so nothing is ever pasted.
Proves: real mic -> device -> Wi-Fi -> Core STT -> cleanup -> Core response. Does NOT prove the
final Ctrl+V into a normal window (that needs a focusable window).

  python scripts/flow_mic_clip.py --runs 3
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
import winsound
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import flow_mic_e2e as h  # noqa: E402
from mazhost.flow import foreground, typable  # noqa: E402


def clipboard() -> str:
    import tkinter as tk

    r = tk.Tk()
    r.withdraw()
    try:
        return r.clipboard_get()
    except tk.TclError:
        return ""
    finally:
        r.destroy()


def clear_clipboard() -> None:
    import ctypes

    u = ctypes.windll.user32
    u.OpenClipboard.argtypes = [ctypes.c_void_p]
    for _ in range(20):  # another app may hold it for a moment
        if u.OpenClipboard(None):
            u.EmptyClipboard()
            u.CloseClipboard()
            return
        time.sleep(0.05)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default="http://127.0.0.1:8787")
    ap.add_argument("--phrase", default="Hey Piblo flow test one two three, buy milk and call Sam.")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--flow-dir", default=str(Path.home() / ".maz-pocket" / "flow"))
    a = ap.parse_args()

    core = h.Core(a.core, h.read_token())
    core.call("/health")
    shots = Path(tempfile.gettempdir()) / "flowmic"
    shots.mkdir(parents=True, exist_ok=True)
    wav = shots / "phrase.wav"
    h.make_wav(a.phrase, wav)
    latency = Path(a.flow_dir) / "latency.log"
    fails = 0
    for n in range(1, a.runs + 1):
        fg = foreground()
        if typable(fg):
            print(f"ABORT run {n}: the focused window could receive a paste; refusing to run")
            return 2
        off = h.log_size(latency)
        clear_clipboard()  # a stale clipboard must never count as this run's result
        core.call("/core/cardputer/key", "POST", {"keys": "esc"})
        time.sleep(0.5)
        core.call("/core/cardputer/key", "POST", {"keys": "y"})
        time.sleep(1.5)
        core.call("/core/cardputer/key", "POST", {"keys": "space"})  # tap 1: start
        time.sleep(1.0)
        (shots / f"clip{n}-listening.png").write_bytes(core.call("/core/cardputer/screen.png", raw=True))
        winsound.PlaySound(str(wav), winsound.SND_FILENAME)
        time.sleep(0.4)
        t2 = time.perf_counter()
        core.call("/core/cardputer/key", "POST", {"keys": "space"})  # tap 2: stop + upload
        time.sleep(1.0)
        got = ""
        while time.perf_counter() - t2 < 20:
            got = clipboard()
            if h.match_ratio(a.phrase, got) >= h.MATCH_MIN:
                break
            time.sleep(0.3)
        ms = round((time.perf_counter() - t2) * 1000)
        (shots / f"clip{n}-result.png").write_bytes(core.call("/core/cardputer/screen.png", raw=True))
        lines = h.new_dictate_lines(latency, off)
        ratio = h.match_ratio(a.phrase, got)
        ok = ratio >= h.MATCH_MIN
        fails += not ok
        print(f"run {n}: {'PASS' if ok else 'FAIL'} ratio={ratio:.2f} tap2->clipboard={ms} ms")
        print(f"  heard: {got!r}")
        print(f"  core:  {lines[-1] if lines else '(no new dictate line)'}")
        print(f"  shots: {shots / f'clip{n}-listening.png'}  {shots / f'clip{n}-result.png'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
