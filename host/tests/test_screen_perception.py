import asyncio
import os
import sys

import pytest
from mazhost.screen_perception import capture_screen, ocr_text


@pytest.mark.skipif(sys.platform != "win32", reason="screen capture needs a Windows desktop")
def test_capture_screen_writes_png(tmp_path):
    pytest.importorskip("PIL")
    # Point the scratch dir at a temp location by monkeypatching the module constant.
    import mazhost.screen_perception as sp
    sp.SCREEN_DIR = tmp_path
    snap = capture_screen()
    assert os.path.exists(snap.path)
    assert snap.path.lower().endswith(".png")
    assert snap.size[0] > 0 and snap.size[1] > 0
    assert snap.source == "screen"
    assert snap.captured_at > 0


def test_ocr_text_missing_image(tmp_path):
    res = ocr_text(str(tmp_path / "does-not-exist.png"))
    assert res["available"] is False
    assert res["reason"] == "image_not_found"


def test_ocr_text_no_engine_installed(tmp_path, monkeypatch):
    import mazhost.screen_perception as sp
    monkeypatch.setattr(sp, "_engine_available", lambda name: False)
    real_import = __import__
    def no_ocr_modules(name, *a, **k):
        if name in ("rapidocr_onnxruntime", "paddleocr"):
            raise ModuleNotFoundError(name)
        return real_import(name, *a, **k)
    monkeypatch.setattr("builtins.__import__", no_ocr_modules)
    image = tmp_path / "blank.png"
    image.write_bytes(b"not really a png")
    res = ocr_text(str(image))
    # No OCR engine installed locally: must not fabricate text, must say why.
    assert res["available"] is False
    assert res["reason"] == "no_ocr_engine_installed"
    assert res["text"] == ""
    assert res["engine"] is None
