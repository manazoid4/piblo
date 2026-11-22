import asyncio
import os
import pytest
from mazhost.screen_perception import capture_screen, ocr_text


def test_capture_screen_writes_png(tmp_path):
    # Point the scratch dir at a temp location by monkeypatching the module constant.
    import mazhost.screen_perception as sp
    sp.SCREEN_DIR = tmp_path
    snap = capture_screen()
    assert os.path.exists(snap.path)
    assert snap.path.lower().endswith(".png")
    assert snap.size[0] > 0 and snap.size[1] > 0
    assert snap.source == "screen"
    assert snap.captured_at > 0


def test_ocr_text_missing_image():
    res = ocr_text("C:/Users/Maz/does-not-exist.png")
    assert res["available"] is False
    assert res["reason"] == "image_not_found"


def test_ocr_text_no_engine_installed(tmp_path):
    import mazhost.screen_perception as sp
    sp.SCREEN_DIR = tmp_path
    snap = capture_screen()
    res = ocr_text(snap.path)
    # No OCR engine installed locally: must not fabricate text, must say why.
    assert res["available"] is False
    assert res["reason"] == "no_ocr_engine_installed"
    assert res["text"] == ""
    assert res["engine"] is None
