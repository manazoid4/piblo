"""LAN Kokoro endpoint for the device: GET /?text=... -> raw s16le mono 24 kHz PCM, streamed per sentence.

HTTP/1.0, no Content-Length, no chunking: the body ends when the connection closes.
Run standalone: python -m mazhost.kokoro_pcm --port 8880 (binds 127.0.0.1; the device needs --host 0.0.0.0 or
MAZ_KOKORO_PCM_BIND=0.0.0.0), or set kokoro_pcm_port in Core.

Access: LAN peers only (security.is_lan_ip). With kokoro_pcm_require_token the shared token is also required
(?k=<token> or an Authorization header). The firmware (pl-b kokoro_stream.cpp) does not send a token yet, so
that flag is off by default; the device needs kokoro_pcm_bind=0.0.0.0 (default is loopback only).
Core-embedded mode loads its own Kokoro model next to SpeechOut's (extra RAM); prefer standalone for that reason.
"""

from __future__ import annotations

import argparse
import hmac
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from .config import Settings
from .security import is_lan_ip
from .speakplan import split_sentences

log = logging.getLogger("uvicorn.error")
MAX_CHARS = 1000
RATE = 24000
MAX_CONCURRENT = 2


def pcm16(audio) -> bytes:
    return (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2").tobytes()


class KokoroSynth:
    """Lazy Kokoro; synth(sentence) -> float32 samples at 24 kHz. Inference is serialized."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._model = None
        self._lock = threading.Lock()

    def check(self) -> None:
        m, v = self.s.kokoro_model_path, self.s.kokoro_voices_path
        if not m or not v or not Path(m).expanduser().is_file() or not Path(v).expanduser().is_file():
            raise RuntimeError("Kokoro model files missing: set kokoro_model_path and kokoro_voices_path")

    def __call__(self, text: str):
        with self._lock:
            if self._model is None:
                from .tts import _use_cuda_if_present
                _use_cuda_if_present()
                from kokoro_onnx import Kokoro
                self._model = Kokoro(str(Path(self.s.kokoro_model_path).expanduser()),
                                     str(Path(self.s.kokoro_voices_path).expanduser()))
            audio, _ = self._model.create(text, voice=self.s.kokoro_voice,
                                          speed=self.s.kokoro_speed, lang="en-us")
            return audio


def make_server(synth, host: str, port: int, token: str | None = None) -> ThreadingHTTPServer:
    """token: when not None, every request must present it (?k= or Authorization, optional 'Bearer ')."""
    slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        timeout = 10  # a stalled client cannot pin a thread

        def _authorized(self, query: dict) -> bool:
            if token is None:
                return True
            given = (query.get("k") or [""])[0] or self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            return bool(token) and hmac.compare_digest(given.encode(), token.encode())

        def log_message(self, *a):  # keep the console quiet
            pass

        def _fail(self, code: int, msg: str, retry_after: bool = False) -> None:
            body = msg.encode()
            self.send_response(code)
            if retry_after:
                self.send_header("Retry-After", "1")
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not is_lan_ip(self.client_address[0]):
                return self._fail(403, "LAN only")
            query = parse_qs(urlparse(self.path).query)
            if not self._authorized(query):
                return self._fail(403, "forbidden")
            text = " ".join(query.get("text", [""])[0].split())
            if not text or len(text) > MAX_CHARS:
                return self._fail(400, f"text required, max {MAX_CHARS} chars")
            check = getattr(synth, "check", None)
            if check:
                try:
                    check()
                except RuntimeError as e:
                    return self._fail(503, str(e))
            if not slots.acquire(blocking=False):
                return self._fail(503, "busy", retry_after=True)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                for sentence in split_sentences(text) or [text]:
                    self.wfile.write(pcm16(synth(sentence)))
                    self.wfile.flush()
            except OSError:  # client gone or write timed out
                pass
            except Exception:
                log.warning("kokoro_pcm synth failed", exc_info=True)  # headers sent; closing ends the body
            finally:
                slots.release()

    return ThreadingHTTPServer((host, port), Handler)


def _token(settings: Settings) -> str | None:
    if not settings.kokoro_pcm_require_token:
        return None
    return settings.token if settings.token_configured else ""  # "" rejects everything


def serve_in_thread(settings: Settings) -> ThreadingHTTPServer | None:
    """Start the endpoint beside Core. A bind failure (port taken) is logged, never fatal to Core."""
    try:
        srv = make_server(KokoroSynth(settings), settings.kokoro_pcm_bind, settings.kokoro_pcm_port, _token(settings))
    except OSError:
        log.warning("kokoro_pcm: cannot bind %s:%s, endpoint disabled", settings.kokoro_pcm_bind,
                    settings.kokoro_pcm_port, exc_info=True)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True, name="kokoro-pcm").start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8880)
    ap.add_argument("--host", default=None)
    a = ap.parse_args()
    cfg = Settings()
    make_server(KokoroSynth(cfg), a.host or cfg.kokoro_pcm_bind, a.port, _token(cfg)).serve_forever()
