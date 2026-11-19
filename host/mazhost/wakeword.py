"""Local streaming wakeword detection for 16 kHz mono PCM16 device audio."""
import logging
import os
from pathlib import Path

import numpy as np

RATE = 16000
MODEL_SUFFIX = "epoch-12-avg-2-chunk-16-left-64.int8.onnx"
log = logging.getLogger("uvicorn.error")
_warned = False


def default_model_dir() -> Path:
    """Where scripts/setup-wakeword.py is documented to install the model."""
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Piblo" / "wakeword-model"


def load(cfg):
    """The "Hey Piblo" gate, or None (no gate) when it is off or cannot start; never fails the session."""
    global _warned
    if not getattr(cfg, "wakeword", True):
        return None
    model_dir = getattr(cfg, "wakeword_model_dir", "") or default_model_dir()
    try:
        if getattr(cfg, "wakeword_engine", "openwakeword") == "openwakeword":
            custom = Path(model_dir) / "hey_piblo.onnx"
            model = getattr(cfg, "wakeword_model", "") or (str(custom) if custom.is_file() else "hey_jarvis")
            return OpenWakeWord(model, getattr(cfg, "wakeword_threshold", 0.5))
        return Wakeword(model_dir)
    except Exception as error:  # engine or model missing: live voice runs ungated, as before
        if not _warned:
            _warned = True
            log.warning("live: Hey Piblo wake word unavailable, listening without it: %s", error)
        return None


class Wakeword:
    def __init__(self, model_dir: str | Path, keywords_file: str | Path | None = None):
        model_dir = Path(model_dir)
        missing = [n for n in ("tokens.txt", *(f"{p}-{MODEL_SUFFIX}" for p in ("encoder", "decoder", "joiner")))
                   if not (model_dir / n).is_file()]
        if missing:  # checked here: sherpa-onnx may abort the process on a missing file
            raise FileNotFoundError(f"wakeword model incomplete in {model_dir}: {', '.join(missing)}")
        import sherpa_onnx

        self.spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(model_dir / "tokens.txt"),
            encoder=str(model_dir / f"encoder-{MODEL_SUFFIX}"),
            decoder=str(model_dir / f"decoder-{MODEL_SUFFIX}"),
            joiner=str(model_dir / f"joiner-{MODEL_SUFFIX}"),
            keywords_file=str(keywords_file or Path(__file__).with_name("hey_piblo_keywords.txt")),
            num_threads=1,
        )
        self.stream = self.spotter.create_stream()

    def reset(self):
        self.stream = self.spotter.create_stream()

    def feed(self, pcm: bytes, sample_rate: int = RATE) -> bool:
        """Feed little-endian PCM16 and return true once for each detection."""
        if sample_rate != RATE or len(pcm) % 2:
            raise ValueError("wakeword audio must be 16 kHz mono PCM16")
        if not pcm:
            return False
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self.stream.accept_waveform(RATE, samples)
        detected = False
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            if self.spotter.get_result(self.stream):
                detected = True
                self.spotter.reset_stream(self.stream)
        return detected


class OpenWakeWord:
    """openWakeWord (Apache-2.0, github.com/dscripka/openWakeWord) on onnxruntime; 80 ms frames.

    model: a pretrained name (hey_jarvis, alexa, hey_mycroft, ...) or a path to a custom .onnx such as
    hey_piblo.onnx trained with openWakeWord's training notebook.
    """
    FRAME = 1280  # 80 ms at 16 kHz, the model's native step

    def __init__(self, model: str, threshold: float = 0.5):
        from openwakeword import utils
        from openwakeword.model import Model

        if not Path(model).suffix:  # pretrained name: fetch it (and the shared feature models) once
            utils.download_models([model])
        self.model = Model(wakeword_models=[model], inference_framework="onnx")
        self.threshold = threshold
        self._buf = np.empty(0, dtype=np.int16)

    def reset(self):
        self.model.reset()
        self._buf = np.empty(0, dtype=np.int16)

    def feed(self, pcm: bytes, sample_rate: int = RATE) -> bool:
        if sample_rate != RATE or len(pcm) % 2:
            raise ValueError("wakeword audio must be 16 kHz mono PCM16")
        self._buf = np.concatenate([self._buf, np.frombuffer(pcm, dtype="<i2")])
        detected = False
        while len(self._buf) >= self.FRAME:
            frame, self._buf = self._buf[:self.FRAME], self._buf[self.FRAME:]
            if max(self.model.predict(frame).values()) >= self.threshold:
                detected = True
        if detected:
            self.reset()  # one detection per utterance, not one per frame above threshold
        return detected
