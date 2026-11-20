import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "bench_parse", Path(__file__).resolve().parents[2] / "scripts" / "bench_parse.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)

LOG = """boot noise
[health] heap=120000 min=90000 largest=60000 stack=3000 loop_max=40ms pressure=no stall=no
[bench] t=30000 boot_ms=2100 loop_ms=100/2/10/40/0 net_ms=2/300/400/500/0 voice_ms=0/0/0/0/0 audio_gaps=0
[health] heap=110000 min=80000 largest=50000 stack=2800 loop_max=300ms pressure=no stall=yes
[bench] t=60000 boot_ms=2100 loop_ms=300/1/30/300/5 net_ms=0/0/0/0/0 voice_ms=1/3000/3000/3000/0 audio_gaps=2
garbage line [bench] t=1 loop_ms=bad
"""


def test_parse_merges_windows_weighted():
    r = bp.parse(LOG)
    assert r["boot_ms"] == 2100 and r["audio_gaps"] == 2 and r["bench_samples"] == 3
    loop = r["stats"]["loop_ms"]
    assert (loop["count"], loop["min"], loop["max"], loop["over"]) == (400, 1, 300, 5)
    assert loop["mean"] == round((10 * 100 + 30 * 300) / 400)
    assert r["stats"]["net_ms"]["count"] == 2          # empty window ignored
    assert r["stats"]["voice_ms"]["max"] == 3000
    assert (r["min_heap"], r["min_largest"], r["min_stack"]) == (80000, 50000, 2800)


def test_cli_compare_and_missing(tmp_path, capsys):
    a, b = tmp_path / "a.log", tmp_path / "b.log"
    a.write_text(LOG)
    b.write_text(LOG.replace("loop_ms=100/2/10/40/0", "loop_ms=100/2/20/40/0"))
    assert bp.main([str(a), str(b)]) == 0
    assert "loop_ms.mean" in capsys.readouterr().out
    empty = tmp_path / "e.log"
    empty.write_text("nothing\n")
    assert bp.main([str(empty)]) == 1
