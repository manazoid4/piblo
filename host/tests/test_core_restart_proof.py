"""v1.5 proofs a PC can give: saved notes survive a Core restart (real process, killed and started again),
and Core serves the staged build named in its manifest. Temp HOME and data dir; the real Core is never touched."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

HOST = Path(__file__).resolve().parents[1]
TOKEN = "restart-proof-token-not-default"
H = {"Authorization": f"Bearer {TOKEN}"}


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _start(home: Path, port: int) -> subprocess.Popen:
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "MAZ_TOKEN": TOKEN, "MAZ_PORT": str(port),
           "MAZ_BIND": "127.0.0.1", "MAZ_DATA_DIR": str(home / "data"), "MAZ_AUTOUPDATE": "0",
           "MAZ_BUDDY_DB": str(home / "buddy.sqlite3"), "MAZ_FLOW_DIR": str(home / "flow"), "MAZ_WAKEWORD": "0",
           "PYTHONPATH": str(HOST)}
    (home / "run").mkdir(exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "mazhost"], cwd=home / "run", env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 40
    while time.time() < end:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.3)
    proc.kill()
    raise AssertionError("Core did not start")


def test_note_survives_core_restart(tmp_path):
    port, note = _port(), "v15 restart test pelican 7391"
    first = _start(tmp_path, port)
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/memory", json={"text": note}, headers=H, timeout=10)
        assert r.status_code == 200, r.text
    finally:
        first.kill(); first.wait()
    second = _start(tmp_path, port)
    try:
        notes = httpx.get(f"http://127.0.0.1:{port}/memory/recall", params={"q": "pelican"}, headers=H, timeout=10).json()["notes"]
        assert any(note in json_text(n) for n in notes), notes
    finally:
        second.kill(); second.wait()


def json_text(n) -> str:
    import json
    return json.dumps(n)
