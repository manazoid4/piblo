"""Keep Windows from flashing a console window for every child process Core starts."""
from __future__ import annotations

import os
import subprocess
from typing import Any


def no_window() -> dict[str, Any]:
    """Popen/run kwargs that hide the console on Windows; empty elsewhere."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
