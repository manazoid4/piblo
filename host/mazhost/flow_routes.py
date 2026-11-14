"""Flow settings API: input mode, cleanup engine, personal dictionary, recent dictations.

Settings live beside the dictionary in ``flow_dir/settings.json`` and are applied to the
running Flow service in memory, so a change takes effect on the next dictation without a
Core restart. Unknown/absent keys fall back to the config's own defaults.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

INPUT_MODES = ("paste", "type")
CLEANUPS = ("auto", "rules", "groq", "local")
STT_CHOICES = ("auto", "parakeet", "whisper")
DEFAULTS = {"input_mode": "paste", "press_enter": False, "cleanup": "auto", "stt": "auto"}
LATENCY_KEEP = 50
DICTIONARY_MAX = 500
_DICTIONARY_LOCK = threading.Lock()
_SETTINGS_LOCK = threading.Lock()


# ---------------------------------------------------------------- persistence
def settings_path(flow) -> Path:
    return Path(flow.dir) / "settings.json"


def read_settings(flow) -> dict:
    path = settings_path(flow)
    data = dict(DEFAULTS)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                data[key] = stored[key]
    return data


def write_settings(flow, data: dict) -> None:
    path = settings_path(flow)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_settings(flow, data: dict) -> None:
    """Push the stored choices onto the live Flow settings object.

    "auto" must restore the values the config started with: an explicit
    rules/groq/local choice overwrites them, and choosing "auto" again used to
    leave those forced flags stuck on the live object until a Core restart.
    """
    s = flow.s
    if not hasattr(flow, "_flow_config_defaults"):
        flow._flow_config_defaults = (
            getattr(s, "flow_cloud", False),
            getattr(s, "flow_cleanup_llm", False),
            getattr(s, "stt_provider", ""),
        )
    cloud, local_llm, stt_default = flow._flow_config_defaults
    cleanup = data.get("cleanup", "auto")
    if cleanup == "auto":
        s.flow_cloud, s.flow_cleanup_llm = cloud, local_llm
    elif cleanup == "rules":
        s.flow_cloud, s.flow_cleanup_llm = False, False
    elif cleanup == "groq":
        s.flow_cloud, s.flow_cleanup_llm = True, False
    elif cleanup == "local":
        s.flow_cloud, s.flow_cleanup_llm = False, True
    stt = data.get("stt", "auto")
    s.stt_provider = stt_default if stt == "auto" else stt


def effective_cleanup(flow) -> str:
    """What the next dictation will actually use, given keys and current flags."""
    s = flow.s
    if getattr(s, "flow_cloud", False) and (s.groq_api_key or getattr(s, "groq_key", "")):
        return "groq"
    if getattr(s, "flow_cleanup_llm", False):
        return "local"
    return "rules"


# ---------------------------------------------------------------- dictionary
def dictionary_path(flow) -> Path:
    return Path(flow.dir) / "dictionary.txt"


def dictionary_lines(flow) -> list[str]:
    """Whole dictionary lines: ``Term`` or ``Term: misheard, misheard`` (Flow aliases)."""
    lines = getattr(flow, "lines", None)
    return list(lines) if lines is not None else list(flow.words)


def split_line(line: str) -> tuple[str, list[str]]:
    term, _, aliases = str(line).partition(":")
    return term.strip(), [a.strip() for a in aliases.split(",") if a.strip()]


def save_lines(flow, lines: list[str]) -> None:
    """Write every line back, keeping aliases, and refresh the live Flow object.

    ``flow.lines`` is what Flow's dictionary actually applies (aliases included);
    ``flow.words`` stays a plain term list for older code paths.
    """
    path = dictionary_path(flow)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".dictionary-", suffix=".txt")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + ("\n" if lines else ""))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    flow.lines = lines
    flow.words = [split_line(line)[0] for line in lines]


def dictionary_view(flow) -> dict:
    entries = [{"term": term, "aliases": aliases} for term, aliases in map(split_line, dictionary_lines(flow))]
    return {
        "words": [entry["term"] for entry in entries],
        "entries": entries,
        "aliased": sum(1 for entry in entries if entry["aliases"]),
    }


_TERM_CHARS = " -'._&+"


def clean_line(word: str) -> str:
    """Normalise one dictionary entry; ``Term: alias, alias`` is allowed."""
    text = " ".join(str(word).split())
    if not text or len(text) > 120:
        raise HTTPException(400, "word must be 1-120 characters")
    term, _, rest = text.partition(":")
    term = term.strip()
    if not term or len(term) > 64:
        raise HTTPException(400, "word must be 1-64 characters")
    aliases = [a.strip() for a in rest.split(",") if a.strip()]
    for part in [term, *aliases]:
        if len(part) > 64 or any(not (ch.isalnum() or ch in _TERM_CHARS) for ch in part):
            raise HTTPException(400, "word has unsupported characters")
    return term + (": " + ", ".join(aliases) if aliases else "")


# ---------------------------------------------------------------- latency log
def recent_dictations(flow, limit: int = 10) -> list[dict]:
    path = Path(flow.dir) / "latency.log"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-LATENCY_KEEP:]
    except OSError:
        return []
    rows = []
    for line in reversed(lines):
        if "dictate" not in line:
            continue
        fields = _fields(line)
        rows.append(
            {
                "when": line.split(" ")[0],
                "intent": fields.get("intent", ""),
                "stt_ms": _int(fields.get("stt")),
                "clean_ms": _int(fields.get("clean")),
                "total_ms": _int(fields.get("total")),
                "cleanup": fields.get("via", ""),
                "stt_engine": fields.get("stt_via", ""),
                "chars": _int(fields.get("chars")),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


_FIELD = re.compile(r"(\w+)=(\S+)")


def _fields(line: str) -> dict:
    return dict(_FIELD.findall(line))


# ---------------------------------------------------------------- insertion
def insert_text(text: str, enter: bool = False, flow=None) -> str:
    """Insert dictated text into whatever window has focus. Returns the mode used."""
    mode = read_settings(flow).get("input_mode", "paste") if flow is not None else "paste"
    if mode == "type":
        type_text(text)
        if enter:
            _tap(0x0D)
        return "type"
    from .flow import paste as paste_text

    paste_text(text, enter)
    return "paste"


# Windows INPUT layout, spelled with fixed-width ctypes so the size is identical
# on 64-bit Windows and on Linux CI. On 64-bit Windows the union must be at
# least as large as MOUSEINPUT (32 bytes); a KEYBDINPUT-only union is 24 and
# makes sizeof(INPUT) 32, so user32.SendInput rejects the call as invalid.
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
                ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_void_p)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_void_p)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _U)]


def type_text(text: str) -> None:
    """Send literal characters as unicode key events (no clipboard touched)."""
    u = ctypes.windll.user32
    KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
    units = text.encode("utf-16-le")
    events = []
    for index in range(0, len(units), 2):
        code = int.from_bytes(units[index:index + 2], "little")
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            events.append(INPUT(type=1, ki=KEYBDINPUT(0, code, flags, 0, None)))
    if not events:
        return
    array = (INPUT * len(events))(*events)
    sent = u.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise OSError(f"SendInput sent {sent}/{len(events)} events")


def _tap(*vks: int) -> None:
    import ctypes

    u = ctypes.windll.user32
    time.sleep(0.03)
    for vk in vks:
        u.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        u.keybd_event(vk, 0, 2, 0)


def foreground_window() -> dict:
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    length = u.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    u.GetWindowTextW(hwnd, buffer, length + 1)
    return {"hwnd": int(hwnd or 0), "title": buffer.value}


# ---------------------------------------------------------------- API
class FlowSettingsIn(BaseModel):
    input_mode: Literal["paste", "type"] | None = None
    press_enter: bool | None = None
    cleanup: Literal["auto", "rules", "groq", "local"] | None = None
    stt: Literal["auto", "parakeet", "whisper"] | None = None


class WordIn(BaseModel):
    word: str = Field(min_length=1, max_length=64)


def build_flow_router(flow) -> APIRouter:
    apply_settings(flow, read_settings(flow))
    api = APIRouter(tags=["flow"])

    def settings_view() -> dict:
        stored = read_settings(flow)
        return {
            **stored,
            "effective_cleanup": effective_cleanup(flow),
            "stt_engine": getattr(flow.s, "stt_provider", ""),
            "has_groq_key": bool(flow.s.groq_api_key or getattr(flow.s, "groq_key", "")),
            "dictionary_count": len(flow.words),
            "dir": str(flow.dir),
        }

    @api.get("/flow/settings")
    def get_settings():
        return settings_view()

    @api.patch("/flow/settings")
    def patch_settings(body: FlowSettingsIn):
        with _SETTINGS_LOCK:
            stored = read_settings(flow)
            for key, value in body.model_dump(exclude_none=True).items():
                stored[key] = value
            write_settings(flow, stored)
            apply_settings(flow, stored)
        return settings_view()

    @api.get("/flow/dictionary")
    def get_dictionary():
        return dictionary_view(flow)

    @api.post("/flow/dictionary")
    def add_word(body: WordIn):
        word = clean_line(body.word)
        term, aliases = split_line(word)
        with _DICTIONARY_LOCK:
            lines = dictionary_lines(flow)
            for index, line in enumerate(lines):
                existing_term, existing_aliases = split_line(line)
                if existing_term.lower() != term.lower():
                    continue
                known = {a.lower() for a in existing_aliases}
                merged = [*existing_aliases, *(a for a in aliases if a.lower() not in known)]
                if merged != existing_aliases:
                    lines[index] = f"{existing_term}: {', '.join(merged)}"
                    save_lines(flow, lines)
                return dictionary_view(flow)
            if len(lines) >= DICTIONARY_MAX:
                raise HTTPException(400, f"dictionary is full ({DICTIONARY_MAX} words)")
            save_lines(flow, [*lines, word])
            return dictionary_view(flow)

    @api.delete("/flow/dictionary/{word}")
    def remove_word(word: str):
        target = split_line(word)[0].lower()
        with _DICTIONARY_LOCK:
            lines = dictionary_lines(flow)
            keep = [line for line in lines if split_line(line)[0].lower() != target]
            if len(keep) != len(lines):
                save_lines(flow, keep)
            return dictionary_view(flow)

    @api.get("/flow/status")
    def status():
        rows = recent_dictations(flow)
        totals = sorted(r["total_ms"] for r in rows if r["total_ms"])
        p95 = totals[min(len(totals) - 1, int(len(totals) * 0.95))] if totals else 0
        errors = []
        path = Path(flow.dir) / "errors.log"
        try:
            raw = path.read_text(encoding="utf-8").splitlines()
            # Return last 120 chars of each line: keeps error codes, strips long paths
            errors = [line.strip()[-120:] for line in raw[-5:] if line.strip()]
        except OSError:
            pass
        return {
            "recent": rows,
            "count": len(rows),
            "avg_total_ms": round(sum(totals) / len(totals)) if totals else 0,
            "p95_total_ms": p95,
            "errors": errors,
            "effective_cleanup": effective_cleanup(flow),
        }

    @api.get("/flow/target")
    def target():
        try:
            return foreground_window()
        except Exception:  # noqa: BLE001 - non-Windows or no window
            return {"hwnd": 0, "title": ""}

    return api