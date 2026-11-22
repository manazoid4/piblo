"""Screen perception: capture + OCR with graceful fallback.

Capture uses PIL ImageGrab (already a Piblo dependency) so no new package is
required. OCR adapters are tried in order; if none is installed the result is
marked ``available: false`` with a clear reason rather than crashing or
fabricating text. Installing an engine (tesseract / rapidocr_onnxruntime) is
recorded in TEST-BATCH.md as an environment task.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import ImageGrab

SCREEN_DIR = Path(
    "C:/Users/Maz/AppData/Local/hermes/cache/scratch"
) / "screenshots"


@dataclass
class ScreenCapture:
    path: str
    size: tuple[int, int]
    captured_at: float
    source: str = "screen"


def capture_screen() -> ScreenCapture:
    """Grab the primary monitor and persist a PNG. Read-only; no pixels are
    sent anywhere unless a downstream capability explicitly uploads them."""
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    img = ImageGrab.grab()
    path = SCREEN_DIR / f"snap_{int(time.time() * 1000)}.png"
    img.save(path)
    return ScreenCapture(str(path), (img.width, img.height), time.time())


def _engine_available(name: str) -> bool:
    return shutil.which(name) is not None


def ocr_text(image_path: str) -> dict[str, Any]:
    """Run OCR over an image. Returns {text, engine, available, reason}."""
    path = Path(image_path)
    if not path.exists():
        return {"text": "", "engine": None, "available": False, "reason": "image_not_found"}

    # Tesseract CLI (free, local, no network).
    if _engine_available("tesseract"):
        try:
            out = subprocess.run(
                ["tesseract", str(path), "stdout", "-l", "eng"],
                capture_output=True, text=True, timeout=60,
            )
            if out.returncode == 0:
                return {"text": out.stdout.strip(), "engine": "tesseract",
                        "available": True, "reason": ""}
        except (OSError, subprocess.SubprocessError):
            pass

    # Python OCR engines (imported lazily so a missing package doesn't break import).
    for engine, module in (("rapidocr", "rapidocr_onnxruntime"),
                           ("paddleocr", "paddleocr")):
        try:
            __import__(module)
        except ModuleNotFoundError:
            continue
        return {"text": "", "engine": engine, "available": True,
                "reason": "engine_installed_but_not_wired"}

    return {"text": "", "engine": None, "available": False,
            "reason": "no_ocr_engine_installed"}
