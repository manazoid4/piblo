r"""nod Flow PC agent: hold Right Ctrl, speak, release -> cleaned text pasted into the focused window.

Run at login:  pythonw host\nodflow.py   (see host\install-nodflow.ps1)
Env: NOD_CORE_URL (default http://127.0.0.1:8787), MAZ_TOKEN (or MAZ_ENV=path to a .env).
`--wav file.wav` skips the mic (test): post that file and paste the result.
"""
from __future__ import annotations

import io
import os
import sys
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


def main() -> None:
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
    log(f"nodflow started core={url}")
    while True:
        if not down(VK_RCONTROL) & 0x8000:
            time.sleep(0.01)
            continue
        t0, chunks = time.perf_counter(), []
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                            callback=lambda d, *_: chunks.append(bytes(d))):
            while down(VK_RCONTROL) & 0x8000:
                time.sleep(0.01)
        released = time.perf_counter()
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





