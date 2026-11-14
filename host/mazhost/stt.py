from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock

from .config import Settings

log = logging.getLogger(__name__)

# Pinned so a moved/replaced upstream model cannot change what we run (CC-BY-4.0, see NOTICE).
PARAKEET_NAME = "nemo-parakeet-tdt-0.6b-v3"
PARAKEET_REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"
PARAKEET_REV = "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"  # verified on the HF API 2026-11-13


class SpeechToText:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._models: dict[tuple[str, str, str], object] = {}
        self._lock = Lock()
        self._onnx = None
        self._onnx_failed = False
        self._onnx_lock = Lock()  # separate from _lock so a 640 MB download never blocks Whisper

    def whisper_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def available(self) -> bool:
        for mod in ("onnx_asr", "faster_whisper"):
            try:
                __import__(mod)
                return True
            except ImportError:
                pass
        return False

    def _get_onnx(self, download: bool = False):
        """Load Parakeet once. Only warm() (download=True) may touch the network or mark a failure.

        The request path loads from the local cache only, so a missing cache or a custom
        (unpinned) model raises without any download and without poisoning later attempts.
        """
        if self._onnx is not None:
            return self._onnx
        # A request must never wait on warm()'s first-run download: fall through to Whisper instead.
        if download:
            self._onnx_lock.acquire()
        elif not self._onnx_lock.acquire(blocking=False):
            raise RuntimeError("parakeet loading")
        try:
            if self._onnx is not None:
                return self._onnx
            if self._onnx_failed:
                raise RuntimeError("parakeet unavailable")
            try:
                import onnx_asr
                name, quant = self.settings.stt_local_model, self.settings.stt_quantization or None
                path = None
                if name == PARAKEET_NAME:
                    from huggingface_hub import snapshot_download
                    patterns = ["config.json", "vocab.txt", "nemo128.onnx",
                                f"*.{quant}.onnx" if quant else "*.onnx*"]
                    path = snapshot_download(PARAKEET_REPO, revision=PARAKEET_REV,
                                             allow_patterns=patterns, local_files_only=not download)
                elif not download:
                    raise RuntimeError("custom STT model loads only at warm-up")
                self._onnx = onnx_asr.load_model(name, path=path, quantization=quant)
            except Exception as e:  # noqa: BLE001
                if download:
                    self._onnx_failed = True
                    log.warning("parakeet load failed (%s); using Whisper for dictation", type(e).__name__)
                raise
        finally:
            self._onnx_lock.release()
        return self._onnx

    def _parakeet(self, path: Path) -> str:
        # onnx-asr resamples any wav rate to 16 kHz itself; the model punctuates and capitalises.
        return str(self._get_onnx().recognize(str(path))).strip()

    def transcribe_local(self, path: Path) -> tuple[str, str]:
        """Flow dictation only: (text, engine). Parakeet when selected and loadable, else local Whisper."""
        if self.settings.stt_provider == "parakeet":
            try:
                return self._parakeet(path), "parakeet"
            except Exception:  # noqa: BLE001 - already logged once at load; stay local
                pass
        return self.transcribe(path), "whisper"

    def _get_model(self, model_name: str | None = None, device: str | None = None,
                   compute: str | None = None):
        name = model_name or self.settings.whisper_model
        selected_device = device or self.settings.whisper_device
        selected_compute = compute or self.settings.whisper_compute
        # CTranslate2 can report a CUDA device even when the runtime DLLs it
        # needs are absent. CPU int8 remains the zero-friction/3-GB-safe path.
        if selected_device == "auto":
            selected_device = "cpu"
        key = (name, selected_device, selected_compute)
        if key not in self._models:
            with self._lock:
                if key not in self._models:
                    from faster_whisper import WhisperModel
                    self._models[key] = WhisperModel(
                        name,
                        device=selected_device,
                        compute_type=selected_compute,
                        local_files_only=True,  # model installation is separate from a private spoken turn
                    )
        return self._models[key]

    def _transcribe_with(self, path: Path, model_name: str, device: str, compute: str) -> str:
        segments, _ = self._get_model(model_name, device, compute).transcribe(
            str(path), language="en", beam_size=1, vad_filter=True
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    def warm(self) -> None:
        """Load the engines now so the first spoken turn is not +7 s (Whisper measured 7.8 s cold, 1.1 s warm)."""
        try:
            self._get_model(self.settings.whisper_model,
                            "cpu" if self.settings.whisper_device == "auto" else self.settings.whisper_device,
                            self.settings.whisper_compute)
        except Exception:  # first real turn will surface the error
            pass
        if self.settings.stt_provider == "parakeet":
            try:
                from . import private_mode
                self._get_onnx(download=not private_mode.enabled())
            except Exception:  # noqa: BLE001 - failure remembered; Whisper takes over
                pass

    def _groq(self, path: Path, prompt: str) -> str:
        from . import netpool
        # Accept both MAZ_GROQ_API_KEY and MAZ_GROQ_KEY for compatibility
        groq_key = self.settings.groq_api_key or self.settings.groq_key
        with path.open("rb") as f:
            r = netpool.client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                data={"model": "whisper-large-v3-turbo", "language": "en", "temperature": "0",
                      "response_format": "text", "prompt": prompt},
                files={"file": (path.name, f, "audio/wav")},
                timeout=20,
            )
        r.raise_for_status()
        return r.text.strip()

    def transcribe_prompted(self, path: Path, prompt: str) -> str:
        """Groq Whisper when keyed (fast, free); local Whisper otherwise."""
        groq_key = self.settings.groq_api_key or self.settings.groq_key
        from . import private_mode
        if groq_key and not private_mode.enabled():
            try:
                return self._groq(path, prompt)
            except Exception:  # noqa: BLE001 - fall back to local
                pass
        return self.transcribe(path)

    def transcribe_turn(self, path: Path) -> str:
        """Voice-call STT: Groq whisper-large-v3-turbo when keyed (~0.3 s), local CPU Whisper otherwise or on error."""
        return self.transcribe_prompted(path, "")

    def transcribe(self, path: Path) -> str:
        return self._transcribe_with(
            path,
            self.settings.whisper_model,
            "cpu" if self.settings.whisper_device == "auto" else self.settings.whisper_device,
            self.settings.whisper_compute,
        )

    def transcribe_profile(self, path: Path, profile: str) -> str:
        """Teach narration presets with a conservative memory floor.

        ECO and BALANCED always use CPU int8 so screen recording/local LLM work
        cannot unexpectedly fight Whisper for a 3 GB GPU. QUALITY may use the
        globally configured Whisper device/model when the user opted into it.
        """
        choice = (profile or "balanced").lower()
        if choice == "eco":
            return self._transcribe_with(path, "base.en", "cpu", "int8")
        if choice == "quality":
            return self.transcribe(path)
        return self._transcribe_with(path, "small.en", "cpu", "int8")

