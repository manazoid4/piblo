"""scripts/flow_loop.py pure parts: noise SNR maths, WER / misheard aggregation, ledger writer. No TTS or STT models."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import flow_loop as fl  # noqa: E402


def tone(n=16000, f=300):
    return (0.3 * np.sin(2 * np.pi * f * np.arange(n) / 16000)).astype(np.float32)


@pytest.mark.parametrize("snr", [20, 10, 5])
@pytest.mark.parametrize("kind", ["white", "pink"])
def test_mix_hits_requested_snr(kind, snr):
    rng = np.random.default_rng(1)
    sig = tone()
    noise = (fl.white if kind == "white" else fl.pink)(len(sig), rng)
    assert fl.measured_snr(sig, fl.mix_snr(sig, noise, snr)) == pytest.approx(snr, abs=0.05)


def test_mix_tiles_short_noise_and_keeps_length():
    sig = tone(8000)
    out = fl.mix_snr(sig, np.ones(300), 10)
    assert len(out) == len(sig) and fl.measured_snr(sig, out) == pytest.approx(10, abs=0.05)


def test_babble_and_reverb_keep_length_and_level():
    rng = np.random.default_rng(2)
    sig = tone()
    b = fl.babble([tone(5000, 200), tone(9000, 400), tone(7000, 500)], len(sig), rng)
    assert len(b) == len(sig) and fl.rms(b) > 0
    r = fl.reverb(sig, rng)
    assert len(r) == len(sig) and fl.rms(r) == pytest.approx(fl.rms(sig), rel=0.01)
    assert not np.allclose(r, sig)


def test_apply_noise_clean_is_identity_and_conditions_parse():
    rng = np.random.default_rng(3)
    sig, pool = tone(), [tone(4000, 250)] * 4
    assert fl.apply_noise(sig, "clean", rng, pool) is sig
    for cond in fl.NOISES[1:]:
        assert len(fl.apply_noise(sig, cond, rng, pool)) == len(sig)
    assert fl.measured_snr(sig, fl.apply_noise(sig, "babble10", rng, pool)) == pytest.approx(10, abs=0.05)


def test_top_misheard_ranks_and_handles_drops():
    pairs = [("Groq is fast", "Grok is fast")] * 3 + [("Codex wrote it", "codec wrote it")] + [("send it now", "send it")]
    top = dict(fl.top_misheard(pairs, 5))
    assert top[("groq", "grok")] == 3 and top[("codex", "codec")] == 1 and top[("now", "")] == 1
    assert fl.top_misheard(pairs, 5)[0][0] == ("groq", "grok")


def test_group_wer_is_word_weighted():
    def row(cat, want, got):
        return {"cat": cat, "got_intent": "paste", "want_intent": "paste", **fl.score_row(want, got)}
    rows = [row("a", "one two three four", "one two three five"), row("a", "one", "one"), row("b", "alpha beta", "alpha beta")]
    g = fl.group_wer(rows, lambda r: r["cat"])
    assert g["a"]["wer"] == 20.0 and g["a"]["n"] == 2 and g["b"]["wer"] == 0.0 and g["b"]["exact"] == 1
    assert fl.wer_pct(rows) == 14.29


def test_stub_learner_learns_soundalikes_not_style_edits():
    L = fl.StubLearner(None)
    for _ in range(2):
        L.observe("Switch to Grok today.", "Switch to Groq today.", "bench")
    for _ in range(3):  # synonym, case and punctuation edits are decoys
        L.observe("It is a big change.", "It is a large change.", "bench")
        L.observe("It is a big change.", "it is a big change!", "bench")
    assert L.rules("bench") == ["Groq: Grok"]
    assert fl.decoys_learned(L.rules("bench"), {("big", "large")}) == 0
    L.guard([("Grok is not Groq.", "Grok is not Groq.")])
    assert L.rules("bench") == []


def test_ledger_writer_and_gate_value(tmp_path):
    path = tmp_path / "ledger.md"
    assert fl.last_gate_value(path) is None
    res = {"meta": {"date": "2026-11-26 10:00", "git_sha": "abc1234", "engine": "parakeet", "corpus": "v1+v2", "conditions": "14 voices"},
           "summary": {"wer_clean": 3.6, "wer_noisy": 12.5, "p50_ms": 451, "p95_ms": 545, "cpu_pct": 300, "ram_mb": 900, "gate_wer": 3.6},
           "learning": {"rules_learned": 4, "learner": "stub", "decoys_learned": 0, "heldout": {"before": 9.1, "after": 7.2, "regressions": 1}}}
    fl.append_ledger(path, fl.ledger_row(res))
    res["summary"]["gate_wer"] = 4.0
    res["learning"] = None
    fl.append_ledger(path, fl.ledger_row(res))
    text = path.read_text(encoding="utf-8")
    assert text.count(fl.LEDGER_HEAD) == 1
    rows = [ln for ln in text.splitlines() if ln.startswith("| 2026")]
    assert len(rows) == 2 and "4 (stub learner)" in rows[0] and "9.1 to 7.2" in rows[0]
    assert all(len(r.strip("|").split("|")) == 16 for r in rows)
    assert fl.last_gate_value(path) == 4.0


def test_corpus_v2_shape():
    rows = fl.load_corpus("flow_corpus_v2.jsonl", "v2")
    assert 180 <= len(rows) <= 220
    assert {r["cat"] for r in rows} >= {"tech", "everyday", "uk", "code"}
    assert {r["split"] for r in rows} == {"in", "out"}
    assert all(r["intent"] in {"paste", "note", "remind", "claude"} and r["text"] for r in rows)
    assert len(fl.load_corpus("flow_corpus.jsonl", "v1")) == 60
