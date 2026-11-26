r"""nod Flow PC agent: hold Right Ctrl, speak, release -> cleaned text pasted into the focused window.

Run at login:  pythonw host\nodflow.py   (see host\install-nodflow.ps1)
Env: NOD_CORE_URL (default http://127.0.0.1:8787), MAZ_TOKEN (or MAZ_ENV=path to a .env).
`--wav file.wav` skips the mic (test): post that file and paste the result.
"""
from __future__ import annotations

import io
import json
import os
import queue
import statistics
import sys
import threading
import time
import wave
from array import array
from pathlib import Path

import httpx

VK_RCONTROL = 0xA3
MIN_HOLD_S = 0.35  # shorter taps are a normal Ctrl shortcut, not dictation
RATE = 16000
SILENCE_PEAK = 400  # whisper hallucinates on silence; skip near-silent holds
LOG = Path(os.environ.get("NOD_FLOW_LOG", "~/.maz-pocket/flow/agent.log")).expanduser()


def _token() -> str:
    if os.environ.get("MAZ_TOKEN"):
        return os.environ["MAZ_TOKEN"]
    env = Path(os.environ.get("MAZ_ENV", r"~\AppData\Local\MAZ Core\.env")).expanduser()
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        if line.startswith("MAZ_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _port() -> str:
    return os.environ.get("MAZ_PORT", "8787")


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")


def to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def send(client: httpx.Client, wav: bytes, released_at: float, paste_fn=None) -> None:
    """One call = one release = at most one paste."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mazhost.flow import paste
    paste_fn = paste_fn or paste

    r = client.post("/dictate", content=wav, headers={"Content-Type": "audio/wav"})
    r.raise_for_status()
    j = r.json()
    intent, text = j["intent"], j["text"]
    if text and intent in ("paste", "claude"):
        status = paste_fn(text, enter=intent == "claude")
        if status != "pasted":
            log(f"paste_status={status} text_len={len(text)}")
    elapsed = round((time.perf_counter() - released_at) * 1000)
    log(f"release_to_done_ms={elapsed} core_ms={j['ms']} intent={intent} chars={len(text)}")
    print(f"{elapsed} ms  [{intent}] {text}")


# ---- streaming push-to-talk: audio is decoded while the key is held, so release -> text is ~70 ms
def stream_available() -> bool:
    """Opt in with PIBLO_PTT_ENGINE=stream. Default is batch Parakeet through Core: on the 60-phrase
    spoken bench (scripts/flow_accuracy_bench.py, 25 Nov) streaming was 52 ms but 43% word error
    (names, tech words, no punctuation); Parakeet was 445 ms and 3.6%."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mazhost import stt_stream
    return os.environ.get("PIBLO_PTT_ENGINE", "batch") == "stream" and stt_stream.available()


class Hold:
    """One key hold. feed() is called from the audio callback; a worker decodes off that thread."""

    def __init__(self, stt) -> None:
        self.utt, self.q = stt.stream(), queue.Queue()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _run(self) -> None:
        while (c := self.q.get()) is not None:
            self.utt.feed(c)

    def feed(self, pcm: bytes) -> None:
        self.q.put(pcm)

    def release(self) -> str:
        """Drain what is queued, flush, return the transcript."""
        self.q.put(None)
        self.worker.join()
        return self.utt.finish()


def finish_hold(hold: Hold, flow, released: float, paste_fn, hwnd: int = 0, safe_fn=None) -> dict:
    """Release -> final text -> cleanup -> paste (focus-guarded). Logs one `ptt` line with release_to_text."""
    from mazhost.flow import safe_paste

    raw = hold.release()
    stt_ms = round((time.perf_counter() - released) * 1000)
    if not raw.strip():
        return {"text": "", "intent": "paste", "ms": {"release_to_text": stt_ms}}
    result = flow.finish(raw.lower(), "zipformer-stream", stt_ms)
    typed: list[float] = []
    if result["intent"] in ("paste", "claude") and result["text"]:
        result["pc"] = (safe_fn or safe_paste)(result["text"], result["intent"] == "claude", hwnd,
                                               lambda t, enter=False: paste_fn(t, enter=enter, on_typed=lambda: typed.append(time.perf_counter())))
        flow.after_paste(result["text"], result["pc"])
    else:
        result["pc"] = "saved"
    result["ms"]["release_to_text"] = round(((typed[0] if typed else time.perf_counter()) - released) * 1000)
    flow._append("latency.log", f"{time.strftime('%Y-%m-%dT%H:%M:%S')} ptt mode=ptt release_to_text={result['ms']['release_to_text']} "
                 f"stt={stt_ms} clean={result['ms']['clean']} intent={result['intent']} stt_via=zipformer-stream "
                 f"pc={result['pc']} chars={len(result['text'])}")
    return result


def stats(log_path: Path, as_json: bool = False) -> dict:
    """p50/p95 of release_to_text per mode from latency.log. Callable by PibloCraft: `nodflow.py --stats --json`."""
    by_mode: dict[str, list[int]] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []:
        if " ptt " in line and "release_to_text=" in line:
            f = dict(p.split("=", 1) for p in line.split() if "=" in p)
            by_mode.setdefault(f.get("mode", "ptt"), []).append(int(f["release_to_text"]))
    out = {}
    for mode, xs in by_mode.items():
        xs.sort()
        out[mode] = {"n": len(xs), "p50_ms": round(statistics.median(xs)), "p95_ms": xs[min(len(xs) - 1, round(0.95 * (len(xs) - 1)))]}
    print(json.dumps(out) if as_json else "\n".join(f"{m}: n={v['n']} p50={v['p50_ms']} ms p95={v['p95_ms']} ms" for m, v in out.items()) or "no ptt turns logged yet")
    return out


def test_paste_window(client: httpx.Client, wav: bytes) -> str:
    """Paste into our own Tk window, only if it is the foreground window; print what landed."""
    import ctypes
    import functools
    import tkinter as tk

    from mazhost.flow import paste

    root = tk.Tk()
    box = tk.Text(root, width=80, height=5)
    box.pack()
    root.attributes("-topmost", True)
    root.update()
    hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt unlocks SetForegroundWindow
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    box.focus_force()
    for _ in range(20):
        root.update()
        time.sleep(0.02)
    try:
        send(client, wav, time.perf_counter(), paste_fn=functools.partial(paste, only_hwnd=hwnd))
        for _ in range(30):
            root.update()
            time.sleep(0.02)
        got = box.get("1.0", "end").strip()
        print(f"TEST WINDOW TEXT: {got!r}")
        return got
    finally:
        root.destroy()


def test_stream_window(wavs: list[Path]) -> list[dict]:
    """Streaming path end to end on this PC, no mic: each wav is fed at real-time pace, then
    released into a Tk text box we own (real focus guard, real Ctrl+V). Prints/returns per-clip results."""
    import ctypes
    import tkinter as tk

    from mazhost import stt_stream
    from mazhost.config import Settings
    from mazhost.flow import Flow, foreground, paste

    stt, flow = stt_stream.StreamingSTT(), Flow(Settings(), None)
    root = tk.Tk()
    box = tk.Text(root, width=80, height=5)
    box.pack()
    root.attributes("-topmost", True)
    root.update()
    hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    box.focus_force()
    for _ in range(20):
        root.update()
        time.sleep(0.02)
    out = []
    try:
        for w in wavs:
            with wave.open(str(w)) as f:
                pcm = f.readframes(f.getnframes())
            box.delete("1.0", "end")
            hold, t_start = Hold(stt), time.perf_counter()
            chunks = [pcm[i:i + 3200] for i in range(0, len(pcm), 3200)]
            for n, c in enumerate(chunks):
                hold.feed(c)
                time.sleep(max(0, t_start + (n + 1) * 0.1 - time.perf_counter()))
                root.update()
            released = time.perf_counter()
            box_out: list[dict] = []
            th = threading.Thread(target=lambda: box_out.append(finish_hold(hold, flow, released, paste, foreground())))
            th.start()
            while th.is_alive():  # keep the Tk window pumping so it reads the clipboard while paste() waits
                root.update()
                time.sleep(0.002)
            r = box_out[0]
            for _ in range(10):
                root.update()
                time.sleep(0.01)
            r["landed"] = box.get("1.0", "end").strip()
            out.append(r)
            print(f"{r['ms']['release_to_text']} ms  [{r['intent']}] {r['text']!r} landed={r['landed']!r}")
    finally:
        root.destroy()
    return out


def main() -> None:
    if "--stream-test" in sys.argv:  # --stream-test a.wav b.wav ... : writes the ptt lines to $MAZ_FLOW_DIR/latency.log
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        test_stream_window([Path(a) for a in sys.argv[sys.argv.index("--stream-test") + 1:]])
        return
    if "--stats" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from mazhost.config import Settings
        stats(Path(Settings().flow_dir).expanduser() / "latency.log", "--json" in sys.argv)
        return
    url = os.environ.get("NOD_CORE_URL", f"http://127.0.0.1:{_port()}")
    client = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {_token()}"}, timeout=30)
    if "--wav" in sys.argv:
        wav = Path(sys.argv[sys.argv.index("--wav") + 1]).read_bytes()
        if "--dry-run" in sys.argv:  # post only, paste nothing
            send(client, wav, time.perf_counter(), paste_fn=lambda t, enter=False: None)
        else:  # paste into a window we launch and verify; never the user's focused window
            test_paste_window(client, wav)
        return

    import ctypes
    import sounddevice as sd

    down = ctypes.windll.user32.GetAsyncKeyState
    streaming = None
    if stream_available():
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from mazhost import stt_stream
        from mazhost.config import Settings
        from mazhost.flow import Flow, foreground, paste
        streaming = (stt_stream.StreamingSTT(), Flow(Settings(), None))
    log(f"nodflow started core={url} engine={'stream' if streaming else 'batch'}")
    while True:
        if not down(VK_RCONTROL) & 0x8000:
            time.sleep(0.005)
            continue
        t0, chunks = time.perf_counter(), []
        hold = Hold(streaming[0]) if streaming else None

        def on_audio(d, *_):
            chunks.append(bytes(d))
            if hold:
                hold.feed(bytes(d))

        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=800, callback=on_audio):
            while down(VK_RCONTROL) & 0x8000:
                time.sleep(0.003)
        released = time.perf_counter()
        if hold:
            try:
                short = released - t0 < MIN_HOLD_S or max(map(abs, array("h", b"".join(chunks))), default=0) < SILENCE_PEAK
                r = finish_hold(hold, streaming[1], released, paste if "--dry-run" not in sys.argv else (lambda t, enter=False, on_typed=None: on_typed and on_typed()),
                                foreground()) if not short else hold.release()
                if not short:
                    print(f"{r['ms']['release_to_text']} ms  [{r['intent']}] {r['text']}")
            except Exception as e:  # noqa: BLE001
                log(f"error {type(e).__name__}: {e}")
            continue
        pcm = b"".join(chunks)
        if released - t0 < MIN_HOLD_S or max(map(abs, array("h", pcm)), default=0) < SILENCE_PEAK:
            continue
        try:
            send(client, to_wav(pcm), released,
                 paste_fn=(lambda t, enter=False: None) if "--dry-run" in sys.argv else None)
        except Exception as e:  # noqa: BLE001 - agent must survive any failure
            log(f"error {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()





