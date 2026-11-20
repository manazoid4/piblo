"""Streaming local speech-to-text for push-to-talk dictation.

Audio is fed while the key is held, so at key release only the last chunk and the flush are left:
that is what makes release -> text fast. sherpa-onnx streaming zipformer transducer (Apache-2.0
code and model, k2-fsa / icefall), int8 on CPU. The model is fetched by scripts/setup-stream-stt.py.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

RATE = 16000
MODELS = {  # name -> (HF repo, pinned revision, file suffix). Both Apache-2.0.
    "zipformer-en-20M": ("csukuangfj/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
                         "d42f2d9f7ca24806fb667456a18a9f1b60f70d16", "epoch-99-avg-1.int8.onnx"),
    "zipformer-en-2023-06-26": ("csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26",
                                "672fbf1b30579d6585301139bb363f42a0ad4a24",
                                "epoch-99-avg-1-chunk-16-left-64.int8.onnx"),
    "nemo-fc-80ms": ("csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms",
                     "7d92d611982bb9dde9d8afc2ef30784211e959ed", ""),  # NVIDIA NeMo, 456 MB fp32
}
DEFAULT_MODEL = "zipformer-en-2023-06-26"
TAIL_S = 0.5  # zeros appended at finish so the last word's right context is decoded


def model_dir(name: str = DEFAULT_MODEL) -> Path:
    base = os.environ.get("PIBLO_STT_STREAM_DIR") or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Piblo" / "stt-stream"
    return Path(base) / name


def available(name: str = DEFAULT_MODEL) -> bool:
    d = model_dir(name)
    return all((d / f).is_file() for f in files(name))


def files(name: str = DEFAULT_MODEL) -> list[str]:
    return ["tokens.txt", *(_part(name, p) for p in ("encoder", "decoder", "joiner"))]


def _part(name: str, part: str) -> str:
    suffix = MODELS[name][2]
    return f"{part}-{suffix}" if suffix else f"{part}.onnx"


class StreamingSTT:
    """One loaded model; call stream() per utterance. Not thread-safe across streams."""

    def __init__(self, name: str = DEFAULT_MODEL, threads: int = 2, endpoint_silence_s: float = 0.6) -> None:
        d = model_dir(name)
        missing = [f for f in files(name) if not (d / f).is_file()]
        if missing:  # checked here: sherpa-onnx may abort the process on a missing file
            raise FileNotFoundError(f"streaming STT model incomplete in {d}: {', '.join(missing)}")
        import sherpa_onnx

        self.name = name
        self.rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"), encoder=str(d / _part(name, "encoder")),
            decoder=str(d / _part(name, "decoder")), joiner=str(d / _part(name, "joiner")),
            model_type="nemo_transducer" if name.startswith("nemo") else "",
            num_threads=threads, sample_rate=RATE, feature_dim=80, decoding_method="greedy_search",
            enable_endpoint_detection=True, rule1_min_trailing_silence=endpoint_silence_s,
            rule2_min_trailing_silence=endpoint_silence_s, rule3_min_utterance_length=60.0)

    def stream(self) -> "Utterance":
        return Utterance(self.rec)


class Utterance:
    def __init__(self, rec) -> None:
        self.rec, self.s = rec, rec.create_stream()

    def feed(self, pcm16: bytes) -> None:
        """Decode as audio arrives. Returns nothing; call text() any time for the partial."""
        x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        self.s.accept_waveform(RATE, x)
        while self.rec.is_ready(self.s):
            self.rec.decode_stream(self.s)

    def text(self) -> str:
        return self.rec.get_result(self.s).strip()

    def endpoint(self) -> bool:
        """True once the model has seen trailing silence after speech (voice-detected end-of-speech)."""
        return self.rec.is_endpoint(self.s)

    def finish(self) -> str:
        """Flush the last frames and return the final transcript."""
        self.s.accept_waveform(RATE, np.zeros(int(RATE * TAIL_S), dtype=np.float32))
        self.s.input_finished()
        while self.rec.is_ready(self.s):
            self.rec.decode_stream(self.s)
        return self.text()
