"""Small laptop-side speech output for Call PC.

The Cardputer asks for a WAV; Windows does the synthesis. This keeps voices,
models and credentials off the ESP32 and gives v0.3 spoken replies without
adding another cloud dependency.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import wave
import sys
from array import array
from pathlib import Path

import httpx
import pyttsx3

from . import netpool, private_mode

from .config import Settings
from .voices import ADRIAN_ID, VoiceService


log = logging.getLogger("uvicorn.error")

# Spoken often and identical every time: synthesised once per voice, then served
# from disk (no Fish call, so no ~1-4 s wait). Any reply of <= CACHE_MAX_CHARS is
# cached too ("Done.", "Okay, noted.").
COMMON_PHRASES = frozenset(p.lower() for p in (
    "Sorry, my brain is offline, try again",
    "Sorry, my brain is offline",
    "Okay.", "Okay", "Done.", "Done", "Got it.", "Got it", "Sure.", "Sure", "Noted.",
    "On it.", "One moment.", "Sorry, I didn't catch that.", "Sorry, I did not catch that.",
    "I couldn't reach the PC.", "Something went wrong, try again.", "Hi, I'm Piblo.",
))
CACHE_MAX_CHARS = 40
CACHE_MAX_FILES = 300


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _use_cuda_if_present() -> None:
    """GPU Kokoro: use CUDA only when onnxruntime-gpu AND the pip nvidia-*-cu12 DLLs (cuDNN 9) are present.

    A missing cuDNN DLL kills the whole process natively, so check the files first and fall back to CPU.
    Use the fp32 kokoro model with CUDA; the int8 model is slower on GPU than on CPU.
    """
    os.environ.setdefault("ONNX_PROVIDER", "CPUExecutionProvider")
    try:
        import nvidia, onnxruntime
        libs = list(Path(list(nvidia.__path__)[0]).glob("*/bin"))
        if ("CUDAExecutionProvider" not in onnxruntime.get_available_providers()
                or not any(lib.glob("cudnn_graph64_9.dll") for lib in libs)):
            return
        for lib in libs:
            os.add_dll_directory(str(lib))
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ["PATH"]
        os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
    except (ImportError, OSError, AttributeError):
        pass  # CPU path still works


class SpeechOut:
    def __init__(self, settings: Settings, voices: VoiceService | None = None) -> None:
        self.settings = settings
        self.last_provider = ""
        self.voices = voices or VoiceService(settings)
        self.voices._synth = self._fish_voice
        self.voices.fallback = self.synthesize
        self.cache_hits = 0
        self._kokoro_model = None
        self._kokoro_lock = threading.Lock()

    def _cache_file(self, text: str, voice: str) -> Path | None:
        norm = _norm(text)
        if norm not in COMMON_PHRASES and len(norm) > CACHE_MAX_CHARS:
            return None
        digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:20]
        return self.voices.dir / "tts_cache" / voice / f"{digest}.wav"

    def _cache_store(self, src: Path, cached: Path) -> None:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(".tmp")
            shutil.copyfile(src, tmp)
            tmp.replace(cached)
            files = sorted(cached.parent.glob("*.wav"), key=lambda p: p.stat().st_mtime)
            for old in files[:-CACHE_MAX_FILES]:
                old.unlink(missing_ok=True)
        except OSError:
            log.warning("tts cache write failed", exc_info=True)

    def warm(self) -> None:
        """Load the TTS model and run one short synth so the first spoken turn is not the slow one."""
        if not self.settings.tts_enabled or self.settings.tts_provider != "kokoro":
            return
        try:
            # Straight to the model: synthesize() may answer "Ready." from the disk cache and never load it.
            tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
            try:
                self._kokoro("Ready.", tmp)
            finally:
                tmp.unlink(missing_ok=True)
        except Exception:
            log.warning("tts warmup failed", exc_info=True)

    def available(self) -> bool:
        return self.settings.tts_enabled

    def _fish_voice(self, text: str, path: Path, voice: str) -> None:
        """Single Fish call for an explicit voice (used by cached previews); no fallback."""
        self._fish(text, path, voice, fallback=False)

    def _fish(self, text: str, path: Path, voice: str | None = None, fallback: bool = True) -> None:
        key = self.settings.fish_api_key
        if not key:
            raise RuntimeError("no_fish_key")
        model = self.settings.tts_model
        voice = voice or self.voices.current()
        resp = netpool.client.post(
            "https://api.fish.audio/v1/tts",
            headers={"Authorization": f"Bearer {key}", "model": model},
            json={
                "text": text,
                "reference_id": voice,
                "format": "wav",
                "sample_rate": 16000,
            },
            timeout=30,
        )
        if fallback and resp.status_code in (400, 404, 422) and voice != ADRIAN_ID:
            self.voices.reject(voice)
            return self._fish(text, path, ADRIAN_ID, fallback=False)
        if resp.status_code != 200 or len(resp.content) <= 44:
            raise RuntimeError(f"fish_http_{resp.status_code}:{resp.text[:120]}")
        data = resp.content
        # Fish streams WAV with a bogus length header; rewrite sizes from real byte count.
        i = data.find(b"data")
        if data[:4] == b"RIFF" and i > 0:
            import struct
            data = (data[:4] + struct.pack("<I", len(data) - 8) + data[8:i + 4]
                    + struct.pack("<I", len(data) - i - 8) + data[i + 8:])
        path.write_bytes(data)

    def _kokoro(self, text: str, path: Path) -> None:
        """Optional Kokoro-ONNX, entirely on the Windows Core machine."""
        model_path = Path(self.settings.kokoro_model_path).expanduser()
        voices_path = Path(self.settings.kokoro_voices_path).expanduser()
        if not self.settings.kokoro_model_path or not self.settings.kokoro_voices_path:
            raise RuntimeError("kokoro_model_paths_unset")
        if not model_path.is_file() or not voices_path.is_file():
            raise RuntimeError("kokoro_model_files_missing")
        # ONNX session and voice resources are reused; inference is serialized.
        with self._kokoro_lock:
            if self._kokoro_model is None:
                _use_cuda_if_present()
                from kokoro_onnx import Kokoro
                self._kokoro_model = Kokoro(str(model_path), str(voices_path))
                log.info("kokoro providers=%s", self._kokoro_model.sess.get_providers())
            import soundfile as sf
            audio, rate = self._kokoro_model.create(
                text, voice=self.settings.kokoro_voice,
                speed=self.settings.kokoro_speed, lang="en-us")
            sf.write(str(path), audio, rate, subtype="PCM_16")

    @staticmethod
    def _device_wav(path: Path) -> None:
        """Normalize readable PCM16 WAVs to Cardputer-native mono 16 kHz.

        Malformed provider output is left intact so existing playback error
        reporting and synthetic test fixtures are preserved.
        """
        try:
            with wave.open(str(path), "rb") as wav:
                channels = wav.getnchannels()
                rate = wav.getframerate()
                if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2 or channels not in (1, 2):
                    return
                if channels == 1 and rate == 16000:
                    return
                if rate < 8000 or rate > 96000 or wav.getnframes() > 2_000_000:
                    return
                pcm = wav.readframes(wav.getnframes())
            values = array("h")
            values.frombytes(pcm)
            if sys.byteorder != "little":
                values.byteswap()
            if channels == 2:
                values = array("h", ((values[i] + values[i + 1]) // 2
                                      for i in range(0, len(values) - 1, 2)))
            if rate != 16000 and len(values):
                n = max(1, round(len(values) * 16000 / rate))
                # Lightweight linear interpolation; no dependency on ffmpeg.
                original = values
                values = array("h")
                for i in range(n):
                    pos = i * rate / 16000
                    left = min(int(pos), len(original) - 1)
                    right = min(left + 1, len(original) - 1)
                    frac = pos - int(pos)
                    values.append(round(original[left] * (1 - frac) + original[right] * frac))
            if sys.byteorder != "little":
                values.byteswap()
            temp = path.with_suffix(".native.tmp")
            try:
                with wave.open(str(temp), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    wav.writeframes(values.tobytes())
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        except (OSError, ValueError, EOFError, wave.Error):
            log.warning("TTS WAV format could not be normalized", exc_info=True)

    def synthesize(self, text: str, local_only: bool = False) -> Path:
        if not self.settings.tts_enabled:
            raise RuntimeError("tts_disabled")
        clean = " ".join(text.strip().split())
        if not clean:
            raise RuntimeError("empty_tts_text")
        clean = clean[:1400]

        provider = self.settings.tts_provider
        strict_local = local_only or private_mode.enabled() or not self.settings.fish_api_key
        if local_only or (strict_local and provider == "fish"):
            provider = "kokoro"
        voice = ("kokoro-" + self.settings.kokoro_voice if provider == "kokoro" else
                 "windows" if provider == "windows" else self.voices.current())
        cached = self._cache_file(clean, voice)
        if cached is not None and cached.exists() and cached.stat().st_size > 44:
            handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            handle.close()
            shutil.copyfile(cached, handle.name)
            self.cache_hits += 1
            self.last_provider = "cache"
            log.info("tts_provider=cache bytes=%d", cached.stat().st_size)
            return Path(handle.name)

        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        path = Path(handle.name)
        handle.close()
        reason = ""
        if provider == "kokoro":
            try:
                self._kokoro(clean, path)
                self._device_wav(path)
                self.last_provider = "kokoro"
                if cached is not None:
                    self._cache_store(path, cached)
                return path
            except Exception as error:
                if strict_local:
                    path.unlink(missing_ok=True)
                    raise RuntimeError("kokoro_unavailable") from error
                reason = f"kokoro:{type(error).__name__}"
        elif provider == "fish":
            for _ in range(2):  # Fish first, retried once
                try:
                    self._fish(clean, path)
                    self._device_wav(path)
                    self.last_provider = "fish"
                    if cached is not None:
                        self._cache_store(path, cached)
                    log.info("tts_provider=fish voice=%s model=%s bytes=%d",
                             self.voices.current()[:8] + "...", self.settings.tts_model, path.stat().st_size)
                    return path
                except (RuntimeError, httpx.HTTPError) as error:
                    reason = str(error)
            # Optional fallback to local voice before robotic Windows TTS.
            if self.settings.kokoro_model_path and self.settings.kokoro_voices_path:
                try:
                    self._kokoro(clean, path)
                    self._device_wav(path)
                    self.last_provider = "kokoro"
                    return path
                except Exception as error:
                    reason += f";kokoro:{type(error).__name__}"
        log.warning("TTS FALLBACK reason=%s", reason)
        self.last_provider = "windows"
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", self.settings.tts_rate)
            engine.save_to_file(clean, str(path))
            engine.runAndWait()
            engine.stop()
            if not path.exists() or path.stat().st_size <= 44:
                raise RuntimeError("tts_no_audio")
            self._device_wav(path)
            log.info("tts_provider=windows")
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise
