"""Today's ledger: Documents/Piblo/ledger/YYYY-MM-DD.md, one '## HH:MM kind' block per entry."""
from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()


def ledger_dir() -> Path:
    return Path(os.environ.get("PIBLO_LEDGER_DIR") or Path.home() / "Documents" / "Piblo" / "ledger")


def append_ledger(kind: str, text: str, now: datetime | None = None, directory: Path | None = None) -> Path:
    now = now or datetime.now()
    folder = Path(directory) if directory else ledger_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now:%Y-%m-%d}.md"
    kind = " ".join(str(kind).split()) or "note"
    with _LOCK, path.open("a", encoding="utf-8") as f:
        f.write(f"## {now:%H:%M} {kind}\n{str(text).strip()}\n\n")
    return path
