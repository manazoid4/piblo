"""Remote URL discovery: tells the device (via authed /health) where Core is reachable over Tailscale."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from typing import Callable
from . import private_mode
from .nowindow import no_window

log = logging.getLogger("uvicorn.error.remote")

_TS_NAME = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.ts\.net$")


def _candidates() -> list[str]:
    paths = ["tailscale"]
    if os.name == "nt":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if root:
                paths.append(os.path.join(root, "Tailscale", "tailscale.exe"))
        paths.append(r"C:\Program Files\Tailscale\tailscale.exe")
    return paths


def _run_json(exe: str, args: list[str], timeout: float = 5.0) -> dict | None:
    private_mode.guard_subprocess("Tailscale discovery")
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, **no_window())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _funneled(serve: dict, port: int) -> bool:
    """True when `tailscale serve status --json` shows Funnel enabled for a handler proxying to the port."""
    allow = serve.get("AllowFunnel") or {}
    if not any(allow.values()):
        return False
    needle = (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")
    web = serve.get("Web") or {}
    for site in web.values():
        for handler in ((site or {}).get("Handlers") or {}).values():
            proxy = str((handler or {}).get("Proxy", ""))
            if any(n in proxy for n in needle):
                return True
    return False


def detect(port: int, run_json: Callable[..., dict | None] = _run_json) -> dict:
    """Returns {"remote_url": str|None, "tailnet_ip": str|None}. Never raises."""
    result: dict = {"remote_url": None, "tailnet_ip": None}
    try:
        for exe in _candidates():
            status = run_json(exe, ["status", "--json"])
            if status is None:
                continue
            me = status.get("Self") or {}
            for ip in me.get("TailscaleIPs") or []:
                if isinstance(ip, str) and ip.startswith("100."):
                    result["tailnet_ip"] = ip
                    break
            name = str(me.get("DNSName") or "").rstrip(".")
            if name and _TS_NAME.match(name):
                serve = run_json(exe, ["serve", "status", "--json"])
                if serve and _funneled(serve, port):
                    result["remote_url"] = f"https://{name}"
            break
    except Exception:  # never let discovery break Core
        log.debug("remote detect failed", exc_info=True)
    return result


class RemoteInfo:
    def __init__(self, configured: str, port: int, interval_s: int = 600,
                 detector: Callable[[int], dict] | None = None) -> None:
        self.configured = configured.strip().rstrip("/")
        self.port = port
        self.interval_s = interval_s
        self._detector = detector or detect
        self._lock = threading.Lock()
        self._info: dict = {"remote_url": self.configured or None, "tailnet_ip": None}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def refresh(self) -> None:
        found = self._detector(self.port)
        with self._lock:
            self._info = {
                "remote_url": self.configured or found.get("remote_url"),
                "tailnet_ip": found.get("tailnet_ip"),
            }

    def get(self) -> dict:
        with self._lock:
            return dict(self._info)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                log.debug("remote refresh failed", exc_info=True)
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="remote-detect", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
