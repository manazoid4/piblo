"""On-demand laptop telemetry for the tiny Cardputer status view.

There is deliberately no sampler thread.  A snapshot is cached briefly and is
only refreshed when the Pocket/LAN UI asks for it, so monitoring MAZ does not
become a workload of its own.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from .config import Settings
from . import private_mode
from .nowindow import no_window

CACHE_SECONDS = 2.0


def _mb(value: int | float) -> int:
    return int(round(float(value) / (1024 * 1024)))


def parse_nvidia_smi_line(line: str) -> dict[str, int] | None:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 4:
        return None
    try:
        gpu, used, total, temp = (int(float(part)) for part in parts[:4])
    except ValueError:
        return None
    return {
        "util_pct": max(0, min(gpu, 100)),
        "vram_used_mb": max(0, used),
        "vram_total_mb": max(0, total),
        "temp_c": temp,
    }


class SystemTelemetry:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or private_mode.client(timeout=2)
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        # Prime the non-blocking CPU counter once. psutil documents the first
        # interval=None sample as meaningless; subsequent snapshots are cheap.
        psutil.cpu_percent(interval=None)

    def snapshot(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if not force and self._cached and now - self._cached_at < CACHE_SECONDS:
                return dict(self._cached)

        memory = psutil.virtual_memory()
        battery = psutil.sensors_battery()
        result: dict[str, Any] = {
            "ok": True,
            "cpu_pct": round(psutil.cpu_percent(interval=None), 1),
            "ram_pct": round(float(memory.percent), 1),
            "ram_used_mb": _mb(memory.total - memory.available),
            "ram_total_mb": _mb(memory.total),
            "battery_pct": round(float(battery.percent), 1) if battery else None,
            "charging": bool(battery.power_plugged) if battery else None,
            "gpu": self._gpu(),
            "ollama": self._ollama(),
        }
        with self._lock:
            self._cached = result
            self._cached_at = now
        return dict(result)

    @staticmethod
    def _gpu() -> dict[str, Any]:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return {"available": False}
        try:
            completed = subprocess.run(
                [
                    exe,
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                **no_window(),
            )
        except (OSError, subprocess.SubprocessError):
            return {"available": False}
        if completed.returncode != 0 or not completed.stdout.strip():
            return {"available": False}
        parsed = parse_nvidia_smi_line(completed.stdout.splitlines()[0])
        return {"available": True, **parsed} if parsed else {"available": False}

    def _ollama(self) -> dict[str, Any]:
        # Same key, whichever local engine is configured. Pocket's Laptop view
        # reads `ollama.loaded` and `ollama.model` and should not need to know.
        if self.settings.local_engine == "llamacpp":
            return self._llamacpp()
        try:
            response = self.client.get(f"{self.settings.ollama_url.rstrip('/')}/api/ps", timeout=2)
            response.raise_for_status()
            models = response.json().get("models", [])
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return {"online": False, "loaded": False}
        if not models:
            return {"online": True, "loaded": False}
        item = models[0]
        return {
            "online": True,
            "loaded": True,
            "model": str(item.get("name") or item.get("model") or ""),
            "vram_mb": _mb(item.get("size_vram") or 0),
            "context": int(item.get("context_length") or 0),
            "expires_at": str(item.get("expires_at") or ""),
        }

    def _llamacpp(self) -> dict[str, Any]:
        # llama-server keeps its model resident for its whole lifetime, so an
        # online server is a loaded model. /props reports the fitted context,
        # which is the number that matters after the server clamps -c to VRAM.
        base = self.settings.llamacpp_url.rstrip("/")
        try:
            response = self.client.get(f"{base}/props", timeout=2)
            response.raise_for_status()
            props = response.json()
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return {"online": False, "loaded": False}
        generation = props.get("default_generation_settings") or {}
        model = str(props.get("model_path") or self.settings.llamacpp_model)
        return {
            "online": True,
            "loaded": True,
            "model": Path(model).name or model,
            "vram_mb": 0,
            "context": int(generation.get("n_ctx") or 0),
            "expires_at": "",
        }
