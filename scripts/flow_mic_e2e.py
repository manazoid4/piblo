#!/usr/bin/env python3
"""Remote end-to-end test: the REAL Cardputer mic -> Piblo Core -> text lands in a window on this PC.

  python scripts/flow_mic_e2e.py --dry-run      wiring only: focus, device keys, screenshots, no audio played
  python scripts/flow_mic_e2e.py --runs 3       real run: plays the phrase out of the PC speakers between two space taps

Steps: open Notepad and take focus (tries 4 methods, verified by GetAncestor(GetForegroundWindow(), GA_ROOT)),
read the text back with WM_GETTEXT, send esc, y (Type on PC) and space (start) to Core, play the phrase,
space (stop), save LCD screenshots, wait for text, print timings and the Core latency.log line, then a verdict.

Exit codes: 0 PASS, 1 FAIL (stage named in the output), 2 focus could not be taken (nothing sent to the device),
3 dry run finished (wiring checked, mic NOT exercised, so never a PASS).
Windows only. Standard library + Windows APIs. The token is read from %LOCALAPPDATA%\\Piblo\\Core\\.env, never printed.
"""
from __future__ import annotations

import argparse
import ctypes
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import winsound
from ctypes import wintypes
from pathlib import Path

PHRASE = "Hey Piblo flow test one two three, buy milk and call Sam."
MATCH_MIN = 0.6
GA_ROOT, SW_RESTORE, VK_MENU, KEYUP = 2, 9, 0x12, 0x0002
WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000D, 0x000E

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
HWND, DWORD, BOOL = wintypes.HWND, wintypes.DWORD, wintypes.BOOL
user32.GetForegroundWindow.restype = HWND
user32.GetAncestor.argtypes = [HWND, wintypes.UINT]
user32.GetAncestor.restype = HWND
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
user32.GetWindowThreadProcessId.restype = DWORD
user32.AttachThreadInput.argtypes = [DWORD, DWORD, BOOL]
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [HWND]
user32.SetForegroundWindow.argtypes = [HWND]
user32.SetForegroundWindow.restype = BOOL
user32.SetFocus.argtypes = [HWND]
user32.SetFocus.restype = HWND
user32.SwitchToThisWindow.argtypes = [HWND, BOOL]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, DWORD, ctypes.c_size_t]
user32.SendMessageW.argtypes = [HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_void_p]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [HWND]
kernel32.GetCurrentThreadId.restype = DWORD
WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [HWND, WNDENUMPROC, wintypes.LPARAM]


def say(msg: str = "") -> None:
    print(msg, flush=True)


# ----------------------------------------------------------------- Core API
class CoreError(RuntimeError):
    pass


class Core:
    def __init__(self, base: str, token: str) -> None:
        self.base, self.token = base.rstrip("/"), token

    def call(self, path: str, method: str = "GET", body: dict | None = None, raw: bool = False):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:  # no body echoed: keeps the token out of output
            raise CoreError(f"{path} -> HTTP {e.code}") from None
        except OSError as e:
            raise CoreError(f"{path} -> {type(e).__name__}") from None
        return payload if raw else json.loads(payload or b"null")


def read_token() -> str:
    if os.environ.get("PIBLO_TOKEN"):
        return os.environ["PIBLO_TOKEN"]
    env = Path(os.environ.get("LOCALAPPDATA", "")) / "Piblo" / "Core" / ".env"
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("MAZ_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


# ------------------------------------------------------------------- window
def _class(h: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, buf, 256)
    return buf.value


def top_notepads() -> list[int]:
    found: list[int] = []

    @WNDENUMPROC
    def cb(h, _):
        if user32.IsWindowVisible(h) and _class(h) == "Notepad":
            found.append(h)
        return True

    user32.EnumWindows(cb, 0)
    return found


def edit_of(top: int) -> int:
    found: list[int] = []

    @WNDENUMPROC
    def cb(h, _):
        if "Edit" in _class(h):  # classic "Edit" or Win11 "RichEditD2DPT"
            found.append(h)
            return False
        return True

    user32.EnumChildWindows(top, cb, 0)
    return found[0] if found else 0


def get_text(edit: int) -> str:
    n = user32.SendMessageW(edit, WM_GETTEXTLENGTH, 0, None)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.SendMessageW(edit, WM_GETTEXT, n + 1, ctypes.addressof(buf))
    return buf.value


def set_text(edit: int, text: str) -> None:
    buf = ctypes.create_unicode_buffer(text)
    user32.SendMessageW(edit, WM_SETTEXT, 0, ctypes.addressof(buf))


def root_of_foreground() -> int:
    return user32.GetAncestor(user32.GetForegroundWindow(), GA_ROOT) or 0


def launch_notepad() -> int:
    before = set(top_notepads())
    subprocess.Popen(["notepad.exe"])
    deadline = time.time() + 10
    while time.time() < deadline:
        new = [h for h in top_notepads() if h not in before]
        if new:
            return new[0]
        time.sleep(0.25)
    existing = top_notepads()  # Win11 may open a new tab in an existing window instead
    return existing[-1] if existing else 0


def _m_attach(hwnd: int) -> bool:  # (a) AttachThreadInput to the foreground thread, then SetForegroundWindow
    fg_tid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_tid) and bool(user32.AttachThreadInput(me, fg_tid, True))
    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        return bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_tid, False)


def _m_alt(hwnd: int) -> bool:  # (b) tap ALT first so the foreground lock lets us through
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYUP, 0)
    return bool(user32.SetForegroundWindow(hwnd))


def _m_switch(hwnd: int) -> bool:  # (c) SwitchToThisWindow (undocumented, still exported)
    user32.SwitchToThisWindow(hwnd, True)
    return True


def _m_uia(hwnd: int) -> bool:  # (d) UI Automation; needs the uiautomation package
    import uiautomation as auto  # only imported from a throwaway venv, never installed here

    win = auto.ControlFromHandle(hwnd)
    win.SetActive()
    win.SetFocus()
    return True


FOCUS_METHODS = [
    ("a AttachThreadInput+SetForegroundWindow", _m_attach),
    ("b ALT key+SetForegroundWindow", _m_alt),
    ("c SwitchToThisWindow", _m_switch),
    ("d UI Automation SetActive+SetFocus", _m_uia),
]


def ensure_focus(hwnd: int) -> str | None:
    """Return the name of the method that worked, 'already' if focus was already ours, or None."""
    if root_of_foreground() == hwnd:
        return "already"
    for name, fn in FOCUS_METHODS:
        try:
            fn(hwnd)
        except Exception as e:  # noqa: BLE001 - e.g. uiautomation missing; try the next method
            say(f"    focus {name}: error {type(e).__name__}")
            continue
        time.sleep(0.4)
        ok = root_of_foreground() == hwnd
        say(f"    focus {name}: {'FOREGROUND' if ok else 'not foreground'}")
        if ok:
            return name
    return None


# ------------------------------------------------------------------- audio
def make_wav(text: str, path: Path) -> None:
    esc = text.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono);"
        f"$s.SetOutputToWaveFile('{path}',$f);$s.Speak('{esc}');$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split())


def match_ratio(expected: str, got: str) -> float:
    return difflib.SequenceMatcher(None, norm(expected), norm(got)).ratio()


def new_dictate_lines(log: Path, offset: int) -> list[str]:
    try:
        with log.open("rb") as f:
            f.seek(offset)
            data = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return []
    return [line for line in data.splitlines() if " dictate " in line]


def log_size(log: Path) -> int:
    return log.stat().st_size if log.exists() else 0


def field(line: str, name: str) -> int | None:
    m = re.search(rf"\b{name}=(\d+)", line)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------- run
def one_run(n: int, args, core: Core, edit: int, hwnd: int, wav: Path, shots: Path, latency: Path) -> dict:
    stamp = time.strftime("%H%M%S")

    def shot(tag: str) -> None:
        try:
            p = shots / f"run{n}-{stamp}-{tag}.png"
            p.write_bytes(core.call("/core/cardputer/screen.png", raw=True))
            say(f"    screenshot {tag}: {p}")
        except CoreError as e:
            say(f"    screenshot {tag} failed: {e}")

    set_text(edit, "")
    if not ensure_focus(hwnd):
        return {"verdict": "FAIL", "stage": "focus (lost before the run)"}
    off = log_size(latency)

    core.call("/core/cardputer/key", "POST", {"keys": "esc"})
    time.sleep(0.5)
    core.call("/core/cardputer/key", "POST", {"keys": "y"})  # opens Type on PC
    time.sleep(1.5)
    core.call("/core/cardputer/key", "POST", {"keys": "space"})  # tap 1: start recording
    time.sleep(1.0)
    shot("listening")
    if args.dry_run:
        time.sleep(3.0)  # no audio: the device should record silence
    else:
        winsound.PlaySound(str(wav), winsound.SND_FILENAME)  # synchronous: blocks until the phrase ends
    time.sleep(0.5)
    if not ensure_focus(hwnd):  # the phrase must land in our window, so re-take focus before stopping
        return {"verdict": "FAIL", "stage": "focus (lost while the phrase played)"}
    t2 = time.perf_counter()
    core.call("/core/cardputer/key", "POST", {"keys": "space"})  # tap 2: stop, upload, STT, paste
    time.sleep(1.0)
    shot("working")

    got, landed = "", None
    deadline = t2 + args.wait
    while time.perf_counter() < deadline:
        got = get_text(edit)
        if got.strip():
            landed = time.perf_counter()
            break
        time.sleep(0.25)
    shot("result")

    lines = new_dictate_lines(latency, off)
    line = lines[-1] if lines else ""
    chars = field(line, "chars")
    ratio = match_ratio(args.phrase, got) if landed else 0.0
    res = {"got": got, "ratio": ratio, "latency_line": line,
           "tap_to_text_ms": round((landed - t2) * 1000) if landed else None}

    if args.dry_run:
        res["verdict"] = "DRY-RUN"
        res["stage"] = ("device uploaded nothing (taps or Wi-Fi)" if not lines
                        else f"mic path not exercised (silence: chars={chars})")
    elif landed is None:
        if not lines:
            res.update(verdict="FAIL", stage="device keys (no upload reached Core within the wait)")
        elif chars == 0:
            res.update(verdict="FAIL", stage="mic captured nothing (Core got an empty recording)")
        else:
            res.update(verdict="FAIL", stage="paste (Core transcribed audio but nothing landed in the window)")
    elif ratio < MATCH_MIN:
        res.update(verdict="FAIL", stage="STT wrong (text landed but does not match the phrase)")
    elif not chars:
        res.update(verdict="FAIL", stage="unclear (text landed but Core log shows no audio)")
    else:
        res.update(verdict="PASS", stage="-")
    return res


def main() -> int:
    if os.name != "nt":
        say("FAIL: Windows only")
        return 1
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", default="http://127.0.0.1:8787")
    ap.add_argument("--phrase", default=PHRASE)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--wait", type=float, default=20.0, help="seconds after the second tap for text to land")
    ap.add_argument("--dry-run", action="store_true", help="no audio played; wiring check only")
    ap.add_argument("--flow-dir", default=str(Path.home() / ".maz-pocket" / "flow"))
    args = ap.parse_args()

    token = read_token()
    if not token:
        say("FAIL: no MAZ_TOKEN in %LOCALAPPDATA%\\Piblo\\Core\\.env")
        return 1
    core = Core(args.core, token)
    try:
        core.call("/health")
        say(f"core: up at {args.core}")
    except CoreError as e:
        say(f"FAIL: Core unreachable ({e})")
        return 1

    shots = Path(tempfile.gettempdir()) / "flowmic"
    shots.mkdir(parents=True, exist_ok=True)
    wav = shots / "phrase.wav"
    if not args.dry_run:
        make_wav(args.phrase, wav)
        say(f"phrase WAV: {wav}")

    say("step 1: open Notepad and take focus")
    hwnd = launch_notepad()
    if not hwnd:
        say("FAIL stage=focus: Notepad window never appeared")
        return 2
    edit = edit_of(hwnd)
    if not edit:
        say("FAIL stage=readback: Notepad edit control not found")
        return 1
    method = ensure_focus(hwnd)
    if not method:
        say("FAIL stage=focus: all four methods failed; nothing was sent to the device")
        return 2
    say(f"  focus OK via {method}")

    say("step 2: read-back self-check (WM_SETTEXT probe, WM_GETTEXT read)")
    set_text(edit, "flowmic-probe")
    time.sleep(0.2)
    got = get_text(edit)
    set_text(edit, "")
    if got != "flowmic-probe":
        say(f"FAIL stage=readback: WM_GETTEXT returned {got!r}")
        return 1
    say("  readback OK")

    say(f"step 3: {'DRY RUN (no audio)' if args.dry_run else f'{args.runs} real run(s)'}")
    latency = Path(args.flow_dir) / "latency.log"
    results = []
    for n in range(1, (1 if args.dry_run else args.runs) + 1):
        say(f"  run {n}:")
        r = one_run(n, args, core, edit, hwnd, wav, shots, latency)
        results.append(r)
        say(f"    window text: {r.get('got', '')!r}")
        if r.get("tap_to_text_ms") is not None:
            say(f"    second tap -> text landed: {r['tap_to_text_ms']} ms")
        if "ratio" in r and r.get("got"):
            say(f"    fuzzy match vs phrase: {r['ratio']:.2f} (need >= {MATCH_MIN})")
        say(f"    Core latency.log: {r.get('latency_line') or '(no new dictate line)'}")
        say(f"    VERDICT {r['verdict']}: {r['stage']}")

    say("")
    say(f"notepad left open for inspection (hwnd={hwnd}); latency log: {latency}")
    if args.dry_run:
        say("DRY RUN: wiring checked (focus, readback, device keys, screenshots). NOT a PASS: mic not exercised.")
        return 3
    passed = all(r["verdict"] == "PASS" for r in results)
    say("OVERALL: PASS" if passed else "OVERALL: FAIL - " + "; ".join(r["stage"] for r in results))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
