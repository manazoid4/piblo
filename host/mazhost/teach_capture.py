from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .stt import SpeechToText


@dataclass
class Display:
    id: str
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False


@dataclass
class TeachSession:
    session_id: str
    state: str
    created_at: float
    started_at: float
    capture: dict[str, Any]
    directory: str
    recording: str = ""
    transcript: str = ""
    error: str = ""
    marks: list[dict[str, Any]] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)
    process_pid: int = 0


class TeachError(RuntimeError):
    pass


class TeachCapture:
    """Explicit Windows screen capture for Teach-by-Demonstration.

    ffmpeg/gdigrab records a selected display/region and DirectShow captures the
    configured/first available microphone. Recording is CPU-first; Whisper ECO
    and BALANCED are CPU int8 so the baseline does not reserve the user's GPU.
    """

    def __init__(self, settings: Settings, stt: SpeechToText) -> None:
        self.settings = settings
        self.stt = stt
        self.root = Path(settings.teach_dir).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, TeachSession] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def available() -> dict[str, Any]:
        return {
            "windows": os.name == "nt",
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "mode": "ffmpeg-gdigrab+dshow",
        }

    # ------------------------------------------------------------- displays
    def displays(self) -> list[dict[str, Any]]:
        if os.name != "nt":
            return []

        user32 = ctypes.windll.user32
        monitors: list[Display] = []

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", ctypes.c_ulong),
                        ("szDevice", ctypes.c_wchar * 32)]

        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(RECT), ctypes.c_longlong
        )

        def callback(hmonitor, _hdc, _rect, _data):
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(info)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                rect = info.rcMonitor
                idx = len(monitors) + 1
                monitors.append(Display(
                    id=str(info.szDevice) or f"display-{idx}",
                    name=f"Display {idx}",
                    x=int(rect.left), y=int(rect.top),
                    width=int(rect.right - rect.left),
                    height=int(rect.bottom - rect.top),
                    primary=bool(info.dwFlags & 1),
                ))
            return 1

        cb = callback_type(callback)
        user32.EnumDisplayMonitors(0, 0, cb, 0)
        monitors.sort(key=lambda item: (not item.primary, item.x, item.y))
        for i, item in enumerate(monitors, 1):
            item.name = f"Display {i}" + (" (Primary)" if item.primary else "")
        return [asdict(item) for item in monitors]

    def _select_display(self, display_id: str) -> Display:
        rows = [Display(**row) for row in self.displays()]
        if not rows:
            raise TeachError("no_displays_found")
        if display_id in ("", "primary"):
            return next((row for row in rows if row.primary), rows[0])
        for row in rows:
            if row.id == display_id or row.name == display_id:
                return row
        raise TeachError("display_not_found")

    # --------------------------------------------------------------- audio
    def audio_devices(self) -> list[str]:
        """Return DirectShow audio capture names from the installed ffmpeg."""
        ffmpeg = shutil.which("ffmpeg")
        if os.name != "nt" or not ffmpeg:
            return []
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True, text=True, errors="replace", timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        text = (result.stderr or "") + "\n" + (result.stdout or "")
        devices: list[str] = []
        for line in text.splitlines():
            if "(audio)" not in line:
                continue
            match = re.search(r'"([^"]+)"\s*\(audio\)', line)
            if match and match.group(1) not in devices:
                devices.append(match.group(1))
        return devices

    def _audio_device(self, requested: str) -> str:
        if requested.strip():
            return requested.strip()
        if self.settings.teach_audio_device.strip():
            return self.settings.teach_audio_device.strip()
        devices = self.audio_devices()
        return devices[0] if devices else ""

    # --------------------------------------------------------------- capture
    def start(
        self,
        *,
        display_id: str = "primary",
        region: dict[str, int] | None = None,
        fps: int | None = None,
        audio_device: str = "",
        include_cursor: bool = True,
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise TeachError("teach_capture_requires_windows")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise TeachError("ffmpeg_not_found")

        display = self._select_display(display_id)
        selected_audio = self._audio_device(audio_device)
        capture = {
            "display": asdict(display),
            "fps": max(2, min(int(fps or self.settings.teach_fps), 30)),
            "audio_device": selected_audio,
            "audio_auto_selected": bool(selected_audio and not audio_device and not self.settings.teach_audio_device),
            "cursor": bool(include_cursor),
            "resource_profile": self.settings.resource_profile,
            "vram_budget_mb": self.settings.vram_budget_mb,
        }
        x, y, width, height = display.x, display.y, display.width, display.height
        if region:
            rx = int(region.get("x", 0))
            ry = int(region.get("y", 0))
            rw = int(region.get("width", width))
            rh = int(region.get("height", height))
            if rw < 64 or rh < 64 or rx < 0 or ry < 0 or rx + rw > width or ry + rh > height:
                raise TeachError("invalid_region")
            x += rx
            y += ry
            width, height = rw, rh
            capture["region"] = {"x": rx, "y": ry, "width": rw, "height": rh}

        session_id = "teach_" + secrets.token_urlsafe(10)
        directory = self.root / session_id
        frames = directory / "frames"
        frames.mkdir(parents=True, exist_ok=False)
        recording = directory / "recording.mp4"
        now = time.time()
        session = TeachSession(
            session_id=session_id,
            state="recording",
            created_at=now,
            started_at=now,
            capture=capture,
            directory=str(directory),
            recording=str(recording),
        )

        argv = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "gdigrab", "-framerate", str(capture["fps"]),
            "-offset_x", str(x), "-offset_y", str(y),
            "-video_size", f"{width}x{height}",
            "-draw_mouse", "1" if include_cursor else "0",
            "-i", "desktop",
        ]
        if selected_audio:
            argv += [
                "-f", "dshow", "-i", f"audio={selected_audio}",
                "-map", "0:v:0", "-map", "1:a:0",
            ]
        argv += [
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
            "-pix_fmt", "yuv420p",
        ]
        if selected_audio:
            argv += ["-c:a", "aac", "-b:a", "96k"]
        argv += [str(recording)]

        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise TeachError(f"recorder_start_failed:{error}") from error
        session.process_pid = process.pid
        with self._lock:
            self._sessions[session_id] = session
            self._processes[session_id] = process
        self._event(session, "session_start", {"capture": capture})
        self._write_manifest(session)
        return self.status(session_id)

    def mark(self, session_id: str, note: str = "") -> dict[str, Any]:
        session = self._get(session_id)
        if session.state != "recording":
            raise TeachError("session_not_recording")
        elapsed_ms = max(0, round((time.time() - session.started_at) * 1000))
        item = {"t": elapsed_ms, "type": "mark", "note": " ".join(note.split())[:240]}
        session.marks.append(item)
        self._event(session, "mark", item)
        self._write_manifest(session)
        return item

    def stop(self, session_id: str, *, transcribe: bool = True) -> dict[str, Any]:
        session = self._get(session_id)
        with self._lock:
            process = self._processes.pop(session_id, None)
        if process and process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write("q\n")
                    process.stdin.flush()
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        session.process_pid = 0
        if not Path(session.recording).exists() or Path(session.recording).stat().st_size < 1024:
            session.state = "failed"
            stderr = ""
            if process and process.stderr:
                try:
                    stderr = process.stderr.read()[-1500:]
                except OSError:
                    pass
            session.error = "recording_missing" + (f": {stderr}" if stderr else "")
            self._write_manifest(session)
            raise TeachError(session.error)

        session.state = "processing"
        self._event(session, "session_stop", {})
        self._write_manifest(session)
        try:
            self._extract_scene_frames(session)
            if transcribe:
                self._transcribe(session)
            session.state = "ready"
        except Exception as error:
            session.state = "ready_partial"
            session.error = f"{type(error).__name__}: {error}"
        self._write_manifest(session)
        return self.status(session_id)

    def _extract_scene_frames(self, session: TeachSession) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return
        target = Path(session.directory) / "frames" / "scene-%04d.jpg"
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", session.recording,
             "-vf", "select='gt(scene,0.10)',scale='min(960,iw)':-2",
             "-vsync", "vfr", "-q:v", "4", str(target)],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        session.frames = [
            str(p) for p in sorted((Path(session.directory) / "frames").glob("scene-*.jpg"))[:160]
        ]
        self._event(session, "scene_frames", {"count": len(session.frames)})

    def _transcribe(self, session: TeachSession) -> None:
        # No audio track means screen + MARKs are still a valid demonstration.
        if not session.capture.get("audio_device"):
            return
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return
        wav = Path(session.directory) / "narration-clean.wav"
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", session.recording,
             "-vn", "-ac", "1", "-ar", "16000", "-af", "afftdn=nf=-25", str(wav)],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not wav.exists() or wav.stat().st_size <= 44:
            return
        session.transcript = self.stt.transcribe_profile(wav, self.settings.teach_voice_profile)
        (Path(session.directory) / "transcript.json").write_text(
            json.dumps({
                "text": session.transcript,
                "profile": self.settings.teach_voice_profile,
                "audio_device": session.capture.get("audio_device", ""),
            }, indent=2),
            encoding="utf-8",
        )
        self._event(session, "transcript", {"chars": len(session.transcript)})

    # --------------------------------------------------------------- state
    def status(self, session_id: str) -> dict[str, Any]:
        session = self._get(session_id)
        result = asdict(session)
        result["elapsed_seconds"] = round(max(0, time.time() - session.started_at), 1) if session.started_at else 0
        result["frame_count"] = len(session.frames)
        result["available"] = self.available()
        return result

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        for manifest in self.root.glob("teach_*/manifest.json"):
            try:
                self._get(manifest.parent.name)
            except TeachError:
                continue
        with self._lock:
            sessions = sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)
        return [self.status(item.session_id) for item in sessions[: max(1, min(limit, 50))]]

    def _get(self, session_id: str) -> TeachSession:
        if not re.fullmatch(r"teach_[A-Za-z0-9_-]{6,40}", session_id):
            raise TeachError("invalid_session_id")
        with self._lock:
            session = self._sessions.get(session_id)
        if session:
            return session
        manifest = self.root / session_id / "manifest.json"
        if not manifest.exists():
            raise TeachError("session_not_found")
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            session = TeachSession(**{
                k: raw[k] for k in TeachSession.__dataclass_fields__ if k in raw
            })
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise TeachError("session_manifest_invalid") from error
        with self._lock:
            self._sessions[session_id] = session
        return session

    def _event(self, session: TeachSession, event_type: str, data: dict[str, Any]) -> None:
        path = Path(session.directory) / "timeline.jsonl"
        record = {
            "t": max(0, round((time.time() - session.started_at) * 1000)) if session.started_at else 0,
            "type": event_type,
            **data,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")

    @staticmethod
    def _write_manifest(session: TeachSession) -> None:
        path = Path(session.directory) / "manifest.json"
        path.write_text(json.dumps(asdict(session), indent=2, default=str), encoding="utf-8")
