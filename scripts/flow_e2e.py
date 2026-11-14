"""Piblo Flow end-to-end check on the real PC (no device needed).

Synthesises a phrase with Windows SAPI, opens a throwaway Tk text box as the
foreground window, posts the WAV to Core /dictate?target=pc-paste exactly as the
Cardputer does, and reports what actually landed in the box plus timings.
`--steal` moves focus away mid-request to prove the clipboard fallback.

  python scripts/flow_e2e.py --core http://127.0.0.1:8787 [--steal]
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx

PHRASE = "um so this is a test of piblo flow on the cardputer"


def token() -> str:
    env = Path(os.environ.get("LOCALAPPDATA", "")) / "Piblo" / "Core" / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("MAZ_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def sapi_wav(text: str, path: Path) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono);"
        f"$s.SetOutputToWaveFile('{path}',$f);$s.Speak('{text}');$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default="http://127.0.0.1:8787")
    ap.add_argument("--steal", action="store_true")
    ap.add_argument("--phrase", default=PHRASE)
    a = ap.parse_args()

    wav = Path(tempfile.gettempdir()) / "piblo_flow_e2e.wav"
    sapi_wav(a.phrase, wav)

    import tkinter as tk

    try:  # Tk coords must match SetCursorPos pixels on scaled displays
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        pass
    root = tk.Tk()
    root.title("Piblo Flow e2e")
    box = tk.Text(root, width=80, height=5)
    box.pack()
    root.attributes("-topmost", True)
    root.update()
    u = ctypes.windll.user32
    hwnd = u.GetAncestor(root.winfo_id(), 2)
    # Windows' foreground lock refuses SetForegroundWindow from a background process,
    # so take focus the legitimate way: a real mouse click inside our own box.
    root.geometry("+200+200")
    root.update()
    x, y = box.winfo_rootx() + 20, box.winfo_rooty() + 10
    u.SetCursorPos(x, y)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    for _ in range(25):
        root.update()
        time.sleep(0.02)
    if u.GetAncestor(u.GetForegroundWindow(), 2) != hwnd:  # never paste into someone else's window
        root.destroy()
        print("ABORT: test window could not take focus; nothing posted")
        return 2

    out: dict = {}

    def post() -> None:
        t0 = time.perf_counter()
        r = httpx.post(f"{a.core}/dictate", params={"target": "pc-paste"}, content=wav.read_bytes(),
                       headers={"Authorization": f"Bearer {token()}", "Content-Type": "audio/wav"},
                       timeout=60)
        out["ms"] = round((time.perf_counter() - t0) * 1000)
        out["status"] = r.status_code
        out["json"] = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text

    th = threading.Thread(target=post)
    th.start()
    other = None
    if a.steal:  # another window takes focus while Core is still transcribing
        time.sleep(0.25)  # after Core has armed on the upload, before STT finishes
        other = tk.Toplevel(root)
        other.title("thief")
        other.geometry("200x80+700+200")
        other.attributes("-topmost", True)
        other.update()
        u.SetCursorPos(780, 230)  # click into the thief: our own window, so focus is allowed
        u.mouse_event(0x0002, 0, 0, 0, 0)
        u.mouse_event(0x0004, 0, 0, 0, 0)
    while th.is_alive():
        root.update()
        time.sleep(0.02)
    for _ in range(25):
        root.update()
        time.sleep(0.02)
    landed = box.get("1.0", "end").strip()
    clip = root.clipboard_get() if a.steal else ""
    root.destroy()
    j = out.get("json", {})
    print(f"status={out.get('status')} round_trip_ms={out.get('ms')} core_ms={j.get('ms') if isinstance(j, dict) else j}")
    print(f"pc={j.get('pc') if isinstance(j, dict) else None!r} text={j.get('text') if isinstance(j, dict) else None!r}")
    print(f"LANDED IN BOX: {landed!r}")
    if a.steal:
        print(f"PC CLIPBOARD: {clip!r}")
        return 0 if (not landed and j.get("pc") == "clipboard") else 1
    return 0 if landed and j.get("pc") == "pasted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
