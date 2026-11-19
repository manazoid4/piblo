import re
import subprocess
from pathlib import Path

from mazhost import nowindow

MAZHOST = Path(nowindow.__file__).parent
SPAWN = re.compile(r"subprocess\.(run|Popen|check_output)\(|create_subprocess_(exec|shell)\(")


def test_no_window_flag_on_windows(monkeypatch):
    monkeypatch.setattr(nowindow.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert nowindow.no_window() == {"creationflags": 0x08000000}


def test_no_window_empty_elsewhere(monkeypatch):
    monkeypatch.setattr(nowindow.os, "name", "posix")
    assert nowindow.no_window() == {}


def test_every_core_spawn_hides_its_console():
    missing = []
    for path in sorted(MAZHOST.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in SPAWN.finditer(text):
            window = text[match.start(): match.start() + 900]
            call = window.split("\n\n")[0]
            if not any(k in call for k in ("no_window()", "creationflags=", "**kwargs")):
                missing.append(f"{path.name}:{text.count(chr(10), 0, match.start()) + 1}")
    assert not missing, f"spawns that can flash a console on Windows: {missing}"
