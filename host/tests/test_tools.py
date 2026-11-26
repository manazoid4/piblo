"""Pure-logic tests for tools/ (port matching, PNG diff, bin check, argument parsing). No device needed."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import _common  # noqa: E402
import piblo_benchmark  # noqa: E402
import piblo_flash  # noqa: E402
import visual_diff  # noqa: E402


def png(w, h, px):
    return visual_diff.encode_rgb(w, h, bytes(px))


def test_port_matching():
    assert _common.is_esp_port(0x303A)
    assert not _common.is_esp_port(0x0403) and not _common.is_esp_port(None)


def test_png_roundtrip_and_diff():
    a = png(2, 2, [0, 0, 0] * 4)
    b = png(2, 2, [0, 0, 0] * 3 + [255, 255, 255])
    assert visual_diff.decode_png(a)[:2] == (2, 2)
    pct, out = visual_diff.diff(a, b)
    assert pct == 25.0 and visual_diff.decode_png(out)[3][-3:] == b"\xff\x00\x00"
    assert visual_diff.diff(a, a)[0] == 0.0
    assert visual_diff.diff(a, b, tol=255)[0] == 0.0
    with pytest.raises(ValueError):
        visual_diff.diff(a, png(1, 1, [0, 0, 0]))


def good_bin(n=100):
    return b"\xe9" + b"x" * 10 + b"Piblo v1.5.6 (abc1234)\nCore" + b"\0" * n


def test_check_bin():
    assert piblo_flash.check_bin(good_bin()) == ("1.5.6", "abc1234")
    with pytest.raises(ValueError):
        piblo_flash.check_bin(b"\x00" + good_bin()[1:])
    with pytest.raises(ValueError):
        piblo_flash.check_bin(b"\xe9junk")
    with pytest.raises(ValueError):
        piblo_flash.check_bin(good_bin(piblo_flash.LIMIT))


def test_version_order_and_stats():
    assert piblo_flash.vtuple("1.10.0") > piblo_flash.vtuple("1.5.6")
    assert piblo_benchmark.pct([1, 2, 3, 4, 5], 50) == 3
    assert piblo_benchmark.summarize([])is None


def test_flash_is_dry_run_without_yes(tmp_path, monkeypatch, capsys):
    b = tmp_path / "fw.bin"
    b.write_bytes(good_bin())
    monkeypatch.setattr(_common.pr, "TOKEN", "t")
    monkeypatch.setattr(_common, "status", lambda: {"version": "1.5.0"})
    piblo_flash.main(["--auto", "--bin", str(b), "--fw-dir", str(tmp_path / "fw")])
    assert "dry run" in capsys.readouterr().out and not (tmp_path / "fw").exists()


def test_flash_refuses_downgrade(tmp_path, monkeypatch):
    b = tmp_path / "fw.bin"
    b.write_bytes(good_bin())
    monkeypatch.setattr(_common.pr, "TOKEN", "t")
    monkeypatch.setattr(_common, "status", lambda: {"version": "1.5.6"})
    with pytest.raises(SystemExit):
        piblo_flash.main(["--bin", str(b), "--yes"])
