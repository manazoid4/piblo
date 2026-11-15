"""SUBTITLES: turn a local video into a timestamped .srt beside it.

Local files only. The video must resolve (symlinks followed) to a path inside
one of the configured media folders and carry a known video extension. Audio
is extracted with ffmpeg when it is installed, otherwise faster-whisper decodes
the container itself. VLC is optional: when present the video can be opened
with the new subtitle loaded. No shell is ever involved; every subprocess gets
an argument list.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .config import Settings
from .stt import SpeechToText


VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".3gp", ".ogv",
})
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
FINAL_STATES = frozenset({"done", "failed", "cancelled"})


class SubtitleError(RuntimeError):
    pass


class _Cancelled(Exception):
    pass


class _TimedOut(Exception):
    pass


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


class Transcriber(Protocol):
    def transcribe(
        self, media: Path, language: str | None
    ) -> tuple[Iterable[Any], float, str]:
        """Return (lazy segments with start/end/text, media seconds, language)."""


# ------------------------------------------------------------------ SRT
def format_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(segments: Iterable[Any]) -> str:
    blocks: list[str] = []
    for segment in segments:
        text = "\n".join(
            line.strip() for line in str(segment.text).replace("\r", "").split("\n") if line.strip()
        )
        if not text:
            continue
        start = max(0.0, float(segment.start))
        end = max(float(segment.end), start + 0.001)
        blocks.append(
            f"{len(blocks) + 1}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{text}\n"
        )
    return "\n".join(blocks)


# ------------------------------------------------------------- paths
def resolve_video(raw: str, roots: list[Path]) -> Path:
    """Resolve a user-picked video and confine it to the allowed media roots."""
    if not roots:
        raise SubtitleError("no_media_folders_configured")
    text = (raw or "").strip()
    if not text or "\x00" in text:
        raise SubtitleError("path_required")
    candidate = Path(text).expanduser()
    options = [candidate] if candidate.is_absolute() else [root / candidate for root in roots]
    for option in options:
        try:
            resolved = option.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not any(resolved.is_relative_to(root) for root in roots):
            raise SubtitleError("path_outside_media_folders")
        if not resolved.is_file():
            raise SubtitleError("not_a_file")
        if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
            raise SubtitleError("not_a_video_file")
        return resolved
    raise SubtitleError("video_not_found")


def subtitle_target(video: Path, overwrite: bool) -> Path:
    """`<name>.srt` beside the video; never clobber a user's own subtitle."""
    primary = video.with_suffix(".srt")
    if overwrite or not primary.exists():
        return primary
    return video.with_name(f"{video.stem}.nod.srt")


def list_videos(roots: list[Path], limit: int = 50, max_depth: int = 3) -> list[dict[str, Any]]:
    found: list[tuple[float, Path]] = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() in VIDEO_EXTENSIONS and not path.is_symlink():
                    try:
                        found.append((path.stat().st_mtime, path))
                    except OSError:
                        continue
            if len(found) > 2000:
                break
    found.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "path": str(path),
            "name": path.name,
            "has_srt": path.with_suffix(".srt").exists(),
        }
        for _, path in found[: max(1, min(limit, 200))]
    ]


# ------------------------------------------------------------ helpers
def find_vlc(configured: str = "") -> str | None:
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    found = shutil.which("vlc")
    if found:
        return found
    if os.name == "nt":
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                exe = Path(base) / "VideoLAN" / "VLC" / "vlc.exe"
                if exe.is_file():
                    return str(exe)
    return None


def open_in_vlc(vlc: str, video: Path, srt: Path) -> None:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([vlc, f"--sub-file={srt}", str(video)], **kwargs)


def _load_pcm16_wav(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1 or wav.getframerate() != 16000:
            raise SubtitleError("unexpected_wav_format")
        frames = wav.readframes(wav.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


class WhisperTranscriber:
    """faster-whisper backed transcriber reusing the shared model cache."""

    def __init__(self, settings: Settings, stt: SpeechToText) -> None:
        self.settings = settings
        self.stt = stt

    def transcribe(self, media: Path, language: str | None) -> tuple[Iterable[Any], float, str]:
        if not self.stt.whisper_available():
            raise SubtitleError("faster_whisper_not_installed")
        model = self.stt._get_model(
            self.settings.subtitle_model, self.settings.whisper_device, self.settings.whisper_compute
        )
        # ffmpeg already produced 16 kHz mono PCM: hand Whisper the samples so
        # this path does not depend on PyAV (newer PyAV releases break
        # faster-whisper 1.1.1's decode_audio). Without ffmpeg, Whisper/PyAV
        # decodes the video container directly.
        source: Any = _load_pcm16_wav(media) if media.suffix.lower() == ".wav" else str(media)
        segments, info = model.transcribe(
            source, language=language, beam_size=1, vad_filter=True
        )
        return segments, float(getattr(info, "duration", 0.0) or 0.0), str(
            getattr(info, "language", language or "") or ""
        )


# --------------------------------------------------------------- jobs
class SubtitleJobs:
    """In-memory subtitle jobs. One transcription runs at a time; others queue."""

    def __init__(
        self,
        settings: Settings,
        transcriber: Transcriber | None = None,
        *,
        ffmpeg: str | None | bool = True,
        vlc_finder: Callable[[], str | None] | None = None,
        vlc_opener: Callable[[str, Path, Path], None] = open_in_vlc,
        timeout_seconds: float | None = None,
    ) -> None:
        self.settings = settings
        self.transcriber = transcriber or WhisperTranscriber(settings, SpeechToText(settings))
        # True = autodetect, None/False = never use ffmpeg, str = explicit path.
        self.ffmpeg = shutil.which("ffmpeg") if ffmpeg is True else (ffmpeg or None)
        self.vlc_finder = vlc_finder or (lambda: find_vlc(settings.vlc_path))
        self.vlc_opener = vlc_opener
        self.timeout = float(timeout_seconds or settings.subtitle_timeout_seconds)
        self._lock = threading.Lock()
        self._worker = threading.Semaphore(1)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel: dict[str, threading.Event] = {}

    @property
    def roots(self) -> list[Path]:
        return self.settings.media_root_paths

    def videos(self, limit: int = 50) -> list[dict[str, Any]]:
        return list_videos(self.roots, limit)

    # -------------------------------------------------------- public API
    def start(
        self, path: str, language: str = "auto", open_vlc: bool = False, overwrite: bool = False
    ) -> dict[str, Any]:
        lang = (language or "auto").strip().lower()
        if lang != "auto" and not LANGUAGE_RE.match(lang):
            raise SubtitleError("invalid_language")
        video = resolve_video(path, self.roots)
        job_id = uuid.uuid4().hex[:12]
        job: dict[str, Any] = {
            "id": job_id,
            "kind": "subtitles",
            "video": str(video),
            "srt": "",
            "language": lang,
            "detected_language": "",
            "open_vlc": bool(open_vlc),
            "overwrite": bool(overwrite),
            "state": "queued",
            "progress": 0,
            "label": "QUEUED",
            "ok": None,
            "error": "",
            "segments": 0,
            "media_seconds": 0.0,
            "duration_ms": 0,
            "audio_source": "",
            "vlc": {"requested": bool(open_vlc), "opened": False, "reason": ""},
            "created": time.time(),
            "started": 0.0,
            "finished": 0.0,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._cancel[job_id] = threading.Event()
            self._prune()
        threading.Thread(
            target=self._run, args=(job_id,), daemon=True, name=f"nod-subs-{job_id}"
        ).start()
        return self.status(job_id)

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise SubtitleError("job_not_found")
            snapshot = dict(job)
            snapshot["vlc"] = dict(job["vlc"])
        if snapshot["state"] not in FINAL_STATES and snapshot["started"]:
            snapshot["elapsed_ms"] = round((time.time() - snapshot["started"]) * 1000)
        return snapshot

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise SubtitleError("job_not_found")
            if job["state"] not in FINAL_STATES:
                self._cancel[job_id].set()
                job["cancel_requested"] = True
                if job["state"] == "queued":
                    self._finish(job, "cancelled", error="cancelled")
        return self.status(job_id)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            values = sorted(self._jobs.values(), key=lambda j: j["created"], reverse=True)
            ids = [j["id"] for j in values[: max(1, min(limit, 50))]]
        return [self.status(i) for i in ids]

    # ---------------------------------------------------------- internals
    def _prune(self) -> None:
        if len(self._jobs) <= 60:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j["state"] in FINAL_STATES),
            key=lambda j: j["created"],
        )
        for job in finished[:20]:
            self._jobs.pop(job["id"], None)
            self._cancel.pop(job["id"], None)

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(fields)

    @staticmethod
    def _finish(job: dict[str, Any], state: str, error: str = "") -> None:
        job["state"] = state
        job["ok"] = state == "done"
        job["error"] = error
        job["finished"] = time.time()
        if job["started"]:
            job["duration_ms"] = round((job["finished"] - job["started"]) * 1000)
        job["label"] = {
            "done": "DONE",
            "cancelled": "CANCELLED",
        }.get(state, f"FAILED: {error}"[:60])
        if state == "done":
            job["progress"] = 100

    def _check(self, job_id: str, deadline: float) -> None:
        if self._cancel[job_id].is_set():
            raise _Cancelled()
        if time.time() > deadline:
            raise _TimedOut()

    def _extract_audio(self, job_id: str, video: Path, target: Path, deadline: float) -> None:
        argv = [
            str(self.ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(target),
        ]
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        try:
            while proc.poll() is None:
                self._check(job_id, deadline)
                time.sleep(0.1)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.returncode != 0:
            raise SubtitleError(f"audio_extraction_failed: {stderr.strip()[-200:]}")

    def _run(self, job_id: str) -> None:
        with self._worker:
            with self._lock:
                job = self._jobs.get(job_id)
                if not job or job["state"] != "queued":
                    return
                job["started"] = time.time()
            deadline = time.time() + self.timeout
            video = Path(job["video"])
            lang = job["language"]
            tmpdir = Path(tempfile.mkdtemp(prefix="nod-subs-"))
            tmp_srt: Path | None = None
            try:
                self._check(job_id, deadline)
                media = video
                if self.ffmpeg:
                    self._update(job_id, state="extracting_audio", label="EXTRACTING AUDIO",
                                 audio_source="ffmpeg")
                    media = tmpdir / "audio.wav"
                    self._extract_audio(job_id, video, media, deadline)
                else:
                    self._update(job_id, audio_source="direct")

                self._update(job_id, state="transcribing", progress=0, label="TRANSCRIBING 0%")
                self._check(job_id, deadline)
                segments_iter, media_seconds, detected = self.transcriber.transcribe(
                    media, None if lang == "auto" else lang
                )
                self._update(job_id, media_seconds=round(media_seconds, 2), detected_language=detected)
                collected: list[Segment] = []
                for seg in segments_iter:
                    self._check(job_id, deadline)
                    collected.append(Segment(float(seg.start), float(seg.end), str(seg.text)))
                    pct = int(min(99, (float(seg.end) / media_seconds) * 100)) if media_seconds > 0 else 0
                    self._update(job_id, progress=pct, segments=len(collected),
                                 label=f"TRANSCRIBING {pct}%")
                self._check(job_id, deadline)

                # Re-confirm confinement right before writing: the video could
                # have been swapped for a symlink while we were transcribing.
                video = resolve_video(str(video), self.roots)
                target = subtitle_target(video, job["overwrite"])
                fd, tmp_name = tempfile.mkstemp(prefix=".nod-", suffix=".srt.tmp", dir=str(target.parent))
                tmp_srt = Path(tmp_name)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(build_srt(collected))
                os.replace(tmp_srt, target)
                tmp_srt = None

                vlc_info = dict(job["vlc"])
                if job["open_vlc"]:
                    vlc = self.vlc_finder()
                    if not vlc:
                        vlc_info.update(opened=False, reason="vlc_not_found")
                    else:
                        try:
                            self.vlc_opener(vlc, video, target)
                            vlc_info.update(opened=True, reason="")
                        except OSError as error:
                            vlc_info.update(opened=False, reason=f"vlc_launch_failed: {error}")
                with self._lock:
                    job = self._jobs[job_id]
                    job.update(srt=str(target), vlc=vlc_info)
                    self._finish(job, "done")
            except _Cancelled:
                with self._lock:
                    self._finish(self._jobs[job_id], "cancelled", error="cancelled")
            except _TimedOut:
                with self._lock:
                    self._finish(self._jobs[job_id], "failed", error="timeout")
            except Exception as error:  # noqa: BLE001 - surface every failure as a job state
                with self._lock:
                    self._finish(self._jobs[job_id], "failed", error=str(error)[:300] or type(error).__name__)
            finally:
                if tmp_srt is not None:
                    tmp_srt.unlink(missing_ok=True)
                shutil.rmtree(tmpdir, ignore_errors=True)
