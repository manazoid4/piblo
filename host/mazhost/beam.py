"""Small durable Beam queue shared by MAZ Core and Piblo.

Beam deliberately moves only short text/URLs.  It is not a remote shell and it
never treats received text as executable input.  Laptop -> Pocket messages are
persisted until the handheld acknowledges them by pulling; Pocket -> laptop
messages are added to history and, on Windows, copied to the clipboard using
stdin so message contents never become PowerShell code.
"""
from __future__ import annotations

import json
import os
import subprocess
from .nowindow import no_window
import threading
import time
import uuid
from pathlib import Path
from typing import Any

MAX_TEXT = 2_000
MAX_QUEUE = 40
MAX_HISTORY = 80


def default_beam_path() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "MAZ Core" / "data"
    else:
        root = Path.home() / ".maz-core" / "data"
    return root / "beam.json"


def classify(text: str) -> str:
    lower = text.strip().lower()
    return "link" if lower.startswith(("https://", "http://")) else "text"


class BeamStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_beam_path()
        self._lock = threading.Lock()
        self._state: dict[str, list[dict[str, Any]]] = {"to_pocket": [], "history": []}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        queue = raw.get("to_pocket", [])
        history = raw.get("history", [])
        if isinstance(queue, list):
            self._state["to_pocket"] = [x for x in queue if isinstance(x, dict)][-MAX_QUEUE:]
        if isinstance(history, list):
            self._state["history"] = [x for x in history if isinstance(x, dict)][-MAX_HISTORY:]

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temp.replace(self.path)

    @staticmethod
    def _message(text: str, direction: str) -> dict[str, Any]:
        clean = " ".join(text.replace("\x00", "").splitlines()).strip()
        if not clean:
            raise ValueError("beam_empty")
        if len(clean) > MAX_TEXT:
            clean = clean[:MAX_TEXT]
        return {
            "id": uuid.uuid4().hex[:12],
            "text": clean,
            "kind": classify(clean),
            "direction": direction,
            "created": int(time.time()),
        }

    def to_pocket(self, text: str) -> dict[str, Any]:
        message = self._message(text, "to_pocket")
        with self._lock:
            self._state["to_pocket"].append(message)
            self._state["to_pocket"] = self._state["to_pocket"][-MAX_QUEUE:]
            self._state["history"].append(message)
            self._state["history"] = self._state["history"][-MAX_HISTORY:]
            self._save_locked()
        return dict(message)

    def pull(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._state["to_pocket"]:
                return None
            message = self._state["to_pocket"].pop(0)
            self._save_locked()
            return dict(message)

    def from_pocket(self, text: str) -> dict[str, Any]:
        message = self._message(text, "from_pocket")
        message["clipboard"] = self._copy_to_clipboard(message["text"])
        with self._lock:
            self._state["history"].append(message)
            self._state["history"] = self._state["history"][-MAX_HISTORY:]
            self._save_locked()
        return dict(message)

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        count = max(1, min(int(limit), MAX_HISTORY))
        with self._lock:
            return [dict(x) for x in self._state["history"][-count:]][::-1]

    @staticmethod
    def _copy_to_clipboard(text: str) -> bool:
        if os.name != "nt":
            return False
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$input | Set-Clipboard",
                ],
                input=text,
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
                **no_window(),
            )
            return completed.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
