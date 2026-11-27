r"""Piblo Flow improvement loop: speak -> transcribe -> find weaknesses -> simulate corrections -> re-measure -> ledger.

  python scripts/flow_loop.py --quick     # ~5 min: v1 clean SAPI gate set + one pass over the v2 corpus
  python scripts/flow_loop.py --full      # 5 passes over v2 with different speaker/noise pairings
  python scripts/flow_loop.py --quick --gate   # exit 1 if v1 clean SAPI WER rose > 0.5 points over the last ledger row

Run with the Core venv python (needs onnx_asr, sherpa_onnx, psutil):
  %LOCALAPPDATA%\Piblo\Core\.venv\Scripts\python.exe scripts/flow_loop.py --quick

EVERYTHING IS SYNTHETIC: Windows SAPI voices (Hazel en-GB, Zira en-US, 3 rates) and sherpa-onnx VCTK VITS speakers
(8 accents), with numpy-generated white / pink / babble noise and a numpy room impulse. Not a person, not the device
mic, and there is NO measurement of any other dictation product here. Real-mic accuracy still needs a person.

Steps per run: 1 synthesise  2 add noise  3 transcribe through the real Flow pipeline (Parakeet or streaming STT,
then Flow.finish: cleanup, dictionary, prefix routing)  4 rank the weaknesses  5 simulate user corrections on a
held-in split and feed them to the learner  6 re-score a held-out split with the learned dictionary lines
7 write docs/evidence/flow-loop/<timestamp>.json and a row in docs/evidence/FLOW-LOOP-LEDGER.md.

Models are cached under %LOCALAPPDATA%\Piblo\bench\ (never committed).
"""
from __future__ import annotations

import argparse
import collections
import difflib
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "host"))
from flow_stream_bench import norm, pct, wer  # noqa: E402  (reused: same maths as the old numbers)

SR = 16000
CACHE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Piblo" / "bench"
EVIDENCE = ROOT / "docs" / "evidence"
LEDGER = EVIDENCE / "FLOW-LOOP-LEDGER.md"
DATA = json.loads((HERE / "flow_loop_data.json").read_text(encoding="utf-8"))
NOISES = ["clean", "white20", "white10", "white5", "pink20", "pink10", "pink5",
          "babble20", "babble10", "babble5", "reverb"]
GATE_TOLERANCE_PTS = 0.5
LEDGER_HEAD = ("| date | git sha | engine | corpus | conditions | WER % (clean) | WER % (noisy) | p50 ms | p95 ms | CPU % | RAM MB "
               "| rules learned | decoys learned | held-out WER % before to after | regressions | v1 clean SAPI WER % (gate) |")


# ----------------------------------------------------------------------------- audio maths (no models needed)

def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def measured_snr(clean: np.ndarray, mixed: np.ndarray) -> float:
    return 10 * np.log10(np.mean(clean.astype(np.float64) ** 2) / np.mean((mixed - clean).astype(np.float64) ** 2))


def mix_snr(sig: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """sig + noise scaled so 10*log10(P_sig/P_noise) == snr_db over the whole clip. Noise is tiled/cut to length."""
    noise = np.resize(noise, len(sig)).astype(np.float64)
    gain = rms(sig) / (rms(noise) * 10 ** (snr_db / 20) + 1e-12)
    return (sig + gain * noise).astype(np.float32)


def white(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(n)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    """1/f noise: shape white noise's spectrum by 1/sqrt(f)."""
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(spec), dtype=np.float64)
    f[0] = 1.0
    return np.fft.irfft(spec / np.sqrt(f), n)


def babble(others: list[np.ndarray], n: int, rng: np.random.Generator) -> np.ndarray:
    """3-4 other speech clips overlaid at equal level, each tiled and started at a random offset."""
    out = np.zeros(n)
    for o in others:
        o = np.asarray(o, dtype=np.float64)
        o = np.roll(np.resize(o, n), int(rng.integers(0, max(n, 1)))) / (rms(o) + 1e-9)
        out += o
    return out


def reverb(sig: np.ndarray, rng: np.random.Generator, rt60: float = 0.35) -> np.ndarray:
    """Small-room impulse response from exponentially decaying noise, convolved by FFT, level kept."""
    t = np.arange(int(rt60 * SR)) / SR
    ir = rng.standard_normal(len(t)) * np.exp(-6.9 * t / rt60) * 0.35
    ir[0] = 1.0
    size = 1 << int(np.ceil(np.log2(len(sig) + len(ir))))
    out = np.fft.irfft(np.fft.rfft(sig, size) * np.fft.rfft(ir, size), size)[:len(sig)]
    return (out * rms(sig) / (rms(out) + 1e-12)).astype(np.float32)


def apply_noise(sig: np.ndarray, cond: str, rng: np.random.Generator, pool: list[np.ndarray]) -> np.ndarray:
    if cond == "clean":
        return sig
    if cond == "reverb":
        return reverb(sig, rng)
    kind, snr = re.fullmatch(r"(white|pink|babble)(\d+)", cond).groups()
    if kind == "babble":
        k = int(rng.integers(3, 5))
        noise = babble([pool[int(rng.integers(0, len(pool)))] for _ in range(k)], len(sig), rng)
    else:
        noise = (white if kind == "white" else pink)(len(sig), rng)
    return mix_snr(sig, noise, float(snr))


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1, path
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def write_wav(path: Path, x: np.ndarray) -> None:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0.99:
        x = x * (0.99 / peak)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


# ----------------------------------------------------------------------------- scoring and aggregation

def misheard_pairs(ref: str, hyp: str) -> list[tuple[str, str]]:
    """Word-level (expected, heard) differences; '' marks a dropped or inserted word."""
    r, h = norm(ref), norm(hyp)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, r, h, autojunk=False).get_opcodes():
        if tag == "replace" and i2 - i1 == j2 - j1:
            out += list(zip(r[i1:i2], h[j1:j2]))
        elif tag in ("replace", "delete", "insert"):
            out.append((" ".join(r[i1:i2]), " ".join(h[j1:j2])))
    return out


def top_misheard(pairs: list[tuple[str, str]], k: int = 25) -> list[tuple[tuple[str, str], int]]:
    """Ranked (expected, heard) -> count, over (reference, hypothesis) text pairs."""
    c = collections.Counter(p for ref, hyp in pairs for p in misheard_pairs(ref, hyp))
    return c.most_common(k)


def group_wer(rows: list[dict], key) -> dict:
    """Word-weighted WER per group, same weighting as flow_accuracy_bench's summary."""
    g: dict = collections.defaultdict(lambda: {"n": 0, "words": 0, "errs": 0, "intent_ok": 0, "exact": 0})
    for r in rows:
        a = g[key(r)]
        a["n"] += 1
        a["words"] += r["nref"]
        a["errs"] += r["errs"]
        a["intent_ok"] += r["got_intent"] == r["want_intent"]
        a["exact"] += r["exact"]
    return {k: {**v, "wer": round(100 * v["errs"] / max(v["words"], 1), 2)} for k, v in sorted(g.items())}


def score_row(want: str, got: str) -> dict:
    n = len(norm(want))
    return {"nref": n, "errs": round(wer(want, got) * n), "exact": got.strip() == want.strip()}


def wer_pct(rows: list[dict]) -> float | None:
    words = sum(r["nref"] for r in rows)
    return round(100 * sum(r["errs"] for r in rows) / words, 2) if words else None


# ----------------------------------------------------------------------------- ledger

def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or "?"
    except OSError:
        return "?"


def ledger_row(res: dict) -> str:
    m, s, lr = res["meta"], res["summary"], res.get("learning") or {}
    ho = lr.get("heldout") or {}
    fmt = lambda v: "-" if v is None else v  # noqa: E731
    learned = f"{lr['rules_learned']} ({lr['learner']} learner)" if lr else "-"
    cells = [m["date"], m["git_sha"], m["engine"], m["corpus"], m["conditions"], fmt(s["wer_clean"]), fmt(s["wer_noisy"]),
             s["p50_ms"], s["p95_ms"], s["cpu_pct"], s["ram_mb"], learned, lr.get("decoys_learned", "-"),
             f"{ho['before']} to {ho['after']}" if ho else "-", ho.get("regressions", "-"), fmt(s["gate_wer"])]
    return "| " + " | ".join(str(c) for c in cells) + " |"


def append_ledger(path: Path, row: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# Piblo Flow improvement loop ledger\n\n"
            "One row per `scripts/flow_loop.py` run; details in `docs/evidence/flow-loop/<timestamp>.json`.\n"
            "All audio is SYNTHETIC (SAPI + sherpa-onnx VCTK voices, numpy noise). No other dictation product was measured.\n"
            "The last column is the regression gate: WER on the 60 v1 phrases, clean SAPI voices.\n\n"
            + LEDGER_HEAD + "\n" + "|" + "---|" * 16 + "\n", encoding="utf-8")
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(row + "\n")


def last_gate_value(path: Path) -> float | None:
    """The gate column of the newest ledger row, or None if there is none."""
    path = Path(path)
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 16 and re.fullmatch(r"\d+(\.\d+)?", cells[-1]):
            return float(cells[-1])
    return None


# ----------------------------------------------------------------------------- learner (real module, else labelled stub)

def _strip(w: str) -> str:
    return w.strip(".,!?;:\"")


class StubLearner:
    """Same interface as mazhost.flow_learn.Learner, used only until that module exists. Results are labelled
    'stub learner'. Learns a dictionary rule only for a sound-alike substitution (similar spelling, not a pure
    case/punctuation/synonym edit) seen at least once (a user fixing a word once expects it fixed for good)."""

    MIN_COUNT = 1

    def __init__(self, folder) -> None:
        self.counts: collections.Counter = collections.Counter()
        self.removed: set[str] = set()

    def observe(self, pasted: str, final: str, app: str = "") -> str | None:
        a, b = pasted.split(), final.split()
        key = lambda ws: [_strip(w).lower() for w in ws]  # noqa: E731
        rid = None
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, key(a), key(b), autojunk=False).get_opcodes():
            if tag != "replace" or i2 - i1 > 3 or j2 - j1 > 3:
                continue
            alias, term = " ".join(_strip(w) for w in a[i1:i2]), " ".join(_strip(w) for w in b[j1:j2])
            if not re.search("[a-z]", alias.lower()) or alias.lower() == term.lower():
                continue
            if difflib.SequenceMatcher(None, alias.lower().replace(" ", ""), term.lower().replace(" ", "")).ratio() < 0.6:
                continue
            self.counts[(term, alias)] += 1
            rid = f"{term}:{alias}"
        return rid

    def rules(self, app: str = "") -> list[str]:
        by_term: dict[str, list[str]] = collections.defaultdict(list)
        for (term, alias), n in self.counts.items():
            if n >= self.MIN_COUNT and f"{term}:{alias}" not in self.removed:
                by_term[term].append(alias)
        return [f"{t}: {', '.join(sorted(a))}" for t, a in sorted(by_term.items())]

    def undo(self, rule_id: str) -> None:
        self.removed.add(rule_id)

    def guard(self, corpus_pairs, base=None) -> None:
        """Drop a rule whose misheard side is a correct word somewhere in known-good text."""
        for (term, alias) in list(self.counts):
            if any(re.search(rf"(?i)\b{re.escape(alias)}\b", exp) for _, exp in corpus_pairs):
                self.removed.add(f"{term}:{alias}")


def load_learner(folder):
    try:
        from mazhost.flow_learn import Learner  # type: ignore
        return Learner(folder), "real"
    except Exception:  # noqa: BLE001 - not merged yet, or not importable here
        return StubLearner(folder), "stub"


def rule_pairs(lines: list[str]) -> list[tuple[str, str]]:
    return [(t.strip(), a.strip()) for ln in lines for t, _, al in [ln.partition(":")] for a in al.split(",") if a.strip()]


def make_decoys(rows: list[dict], limit: int = 24) -> tuple[list[tuple[str, str]], set]:
    """Stylistic-only edits of text that was already right: synonym, casing, punctuation. -> (pasted, final) pairs."""
    syn, out, banned = DATA["synonyms"], [], set()
    n_style = 0
    for r in rows:
        if r["errs"] or r["want_intent"] != "paste" or len(out) >= limit:
            continue
        text, words = r["want"], set(norm(r["want"]))
        hit = next((k for k in syn if k in words), None)
        if hit and len(out) < limit * 2 // 3:
            out.append((text, re.sub(rf"(?i)\b{hit}\b", syn[hit], text, count=1)))
            banned.add((hit, syn[hit]))
        elif n_style % 2 == 0:
            out.append((text, text[:1].lower() + text[1:]))
            n_style += 1
        else:
            out.append((text, text.rstrip(".") + "!"))
            n_style += 1
    return out, banned


def decoys_learned(rules: list[str], banned: set) -> int:
    words = {w for pair in banned for w in pair}
    return sum(1 for term, alias in rule_pairs(rules)
               if term.lower() == alias.lower() or (alias.lower(), term.lower()) in banned
               or (alias.lower() in words and term.lower() in words))


# ----------------------------------------------------------------------------- speech synthesis (SAPI batch + sherpa VCTK)

def sapi_voices() -> list[str]:
    ps = ("Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          "$s.GetInstalledVoices()|?{$_.Enabled}|%{$_.VoiceInfo.Name}")
    return [v.strip() for v in subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                                              check=True).stdout.splitlines() if v.strip()]


def sapi_batch(jobs: list[dict]) -> None:
    """One PowerShell process for many clips (the old per-clip spawn costs ~1 s each). jobs: path, text, voice, rate."""
    if not jobs:
        return
    jf = Path(tempfile.mkdtemp()) / "jobs.json"
    jf.write_text(json.dumps(jobs), encoding="utf-8")
    ps = ("Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
          "[System.Speech.AudioFormat.AudioChannel]::Mono);"
          f"$j=Get-Content -Raw -Encoding UTF8 '{jf}'|ConvertFrom-Json;"
          "foreach($c in @($j)){$s.SelectVoice($c.voice);$s.Rate=[int]$c.rate;$s.SetOutputToWaveFile($c.path,$f);$s.Speak($c.text);$s.SetOutputToNull()}")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


class Vctk:
    """sherpa-onnx VITS multi-speaker TTS (VCTK, 109 speakers). Model cached under CACHE/tts, downloaded once."""

    def __init__(self) -> None:
        import sherpa_onnx
        d = CACHE / "tts" / DATA["vctk_model"]
        if not (d / "vits-vctk.onnx").exists():
            self.download(d.parent)
        cfg = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(model=str(d / "vits-vctk.onnx"), lexicon=str(d / "lexicon.txt"),
                                                      tokens=str(d / "tokens.txt")), num_threads=4, provider="cpu"))
        self.tts, self.rate = sherpa_onnx.OfflineTts(cfg), None
        self.vocab = {ln.split(" ", 1)[0] for ln in (d / "lexicon.txt").read_text(encoding="utf-8").splitlines() if ln}
        self.oov: collections.Counter = collections.Counter()

    @staticmethod
    def download(folder: Path) -> None:
        import urllib.request
        folder.mkdir(parents=True, exist_ok=True)
        archive = folder / "vits-vctk.tar.bz2"
        print(f"downloading {DATA['vctk_url']} (~145 MB) to {folder}")
        urllib.request.urlretrieve(DATA["vctk_url"], archive)
        subprocess.run(["tar", "-xjf", str(archive), "-C", str(folder)], check=True)

    def say(self, text: str) -> str:
        words = [DATA["respell"].get(w.lower(), w) for w in re.findall(r"[A-Za-z0-9'\-]+", text)]
        spoken = " ".join(words).lower()
        for w in spoken.split():
            if w not in self.vocab:
                self.oov[w] += 1
        return spoken

    def wav(self, text: str, sid: int, path: Path) -> None:
        a = self.tts.generate(self.say(text), sid=sid, speed=1.0)
        x = np.asarray(a.samples, dtype=np.float64)
        try:
            from scipy.signal import resample_poly
            x = resample_poly(x, SR, a.sample_rate)
        except ImportError:  # shortcut: linear interpolation, fine for a bench; install scipy for better quality
            x = np.interp(np.linspace(0, len(x) - 1, int(len(x) * SR / a.sample_rate)), np.arange(len(x)), x)
        write_wav(path, x.astype(np.float32) * 0.8)


# ----------------------------------------------------------------------------- plan

def load_corpus(name: str, tag: str) -> list[dict]:
    rows = [json.loads(ln) for ln in (HERE / name).read_text(encoding="utf-8").splitlines() if ln.strip()]
    for i, r in enumerate(rows):
        r.setdefault("cat", "v1")
        r.setdefault("split", "in" if i % 2 == 0 else "out")
        r["corpus"], r["i"] = tag, i
    return rows


def build_plan(v1: list[dict], v2: list[dict], passes: int, voices: list[str], seed: int = 1234) -> list[dict]:
    """Gate set (v1, clean, SAPI voices exactly as the 25 Nov baseline chose them) + `passes` passes over v2.
    Each v2 phrase gets one (speaker, noise) pair per pass; pairs are shuffled so conditions spread evenly."""
    rates = DATA["sapi_rates"]
    sapi = [{"id": f"sapi-{v.split()[1].lower()}-r{r}", "kind": "sapi", "voice": v, "rate": r,
             "accent": DATA["sapi_accents"].get(v.split()[1].lower(), "?")} for v in voices for r in rates]
    speakers = sapi + [{**s, "kind": "vctk"} for s in DATA["vctk_speakers"]]
    plan = []
    for c in v1:  # baseline recipe: voice i % n, rate (-1, 0, 1)[i % 3]
        v, r = voices[c["i"] % len(voices)], (-1, 0, 1)[c["i"] % 3]
        sp = next(s for s in sapi if s["voice"] == v and s["rate"] == r)
        plan.append({**c, "speaker": sp, "noise": "clean", "gate": True})
    combos = [(s, n) for s in speakers for n in NOISES]
    random.Random(seed).shuffle(combos)
    for k in range(passes):
        for j, c in enumerate(v2):
            sp, noise = combos[(j + k * len(v2)) % len(combos)]
            plan.append({**c, "speaker": sp, "noise": noise, "gate": False})
    return plan


def synth_all(plan: list[dict], clips: Path, need_vctk: bool) -> dict:
    """Clean clip for every plan item (cached by speaker+text). Returns TTS stats."""
    (clips / "clean").mkdir(parents=True, exist_ok=True)
    sapi_jobs, vctk_items = {}, []
    for it in plan:
        sp = it["speaker"]
        key = hashlib.sha1(f"{sp['id']}|{it['say']}".encode()).hexdigest()[:16]
        it["clean_wav"] = clips / "clean" / f"{key}.wav"
        if it["clean_wav"].exists():
            continue
        if sp["kind"] == "sapi":
            sapi_jobs[key] = {"path": str(it["clean_wav"]), "text": it["say"], "voice": sp["voice"], "rate": sp["rate"]}
        else:
            vctk_items.append((it, sp["sid"]))
    t = time.perf_counter()
    sapi_batch(list(sapi_jobs.values()))
    stats = {"sapi_new": len(sapi_jobs), "vctk_new": 0, "tts_oov": {}}
    if vctk_items:
        tts = Vctk()
        for it, sid in vctk_items:
            if not it["clean_wav"].exists():
                tts.wav(it["say"], sid, it["clean_wav"])
                stats["vctk_new"] += 1
        stats["tts_oov"] = dict(tts.oov.most_common(15))
    stats["synth_s"] = round(time.perf_counter() - t, 1)
    return stats


# ----------------------------------------------------------------------------- engine

def make_engine(engine: str):
    """-> (flow, run) where run(wav) = (raw_text, stt_ms). Same engines and in-process Flow as flow_accuracy_bench."""
    os.environ.setdefault("PIBLO_LEDGER_DIR", tempfile.mkdtemp())  # bench text stays out of the real ledger
    from mazhost.config import Settings
    from mazhost.flow import Flow

    flow = Flow(Settings(token="x" * 40, flow_dir=tempfile.mkdtemp()), speech=None)
    if engine == "stream":
        from flow_stream_bench import pcm_of
        from mazhost.stt_stream import StreamingSTT
        stt = StreamingSTT()

        def run(wav: Path):
            utt, pcm = stt.stream(), pcm_of(wav)
            for k in range(0, len(pcm), 3200):  # 100 ms chunks, decoded "while held"
                utt.feed(pcm[k:k + 3200])
            t = time.perf_counter()
            raw = utt.finish()
            return raw, round((time.perf_counter() - t) * 1000)
        return flow, run
    from mazhost.stt import SpeechToText
    stt = SpeechToText(Settings(token="x" * 40, stt_provider="parakeet"))

    def run(wav: Path):
        t = time.perf_counter()
        raw = stt._parakeet(wav)
        return raw, round((time.perf_counter() - t) * 1000)
    return flow, run


def transcribe_all(plan: list[dict], flow, run, clips: Path, seed: int) -> tuple[list[dict], dict]:
    import psutil
    proc = psutil.Process()
    try:
        proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # keep the PC usable while a long run goes
    except (psutil.Error, AttributeError, OSError):
        pass
    pool_paths = [it["clean_wav"] for it in plan]
    run(plan[0]["clean_wav"])  # warm-up, not timed
    out_dir = clips / "run"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, rss = [], 0
    cpu0, wall0 = sum(proc.cpu_times()[:2]), time.perf_counter()
    for n, it in enumerate(plan):
        wav = it["clean_wav"]
        if it["noise"] != "clean":
            rng = np.random.default_rng(zlib.crc32(f"{seed}|{it['corpus']}|{it['i']}|{n}".encode()))
            pool = ([read_wav(pool_paths[int(rng.integers(0, len(pool_paths)))]) for _ in range(6)]
                    if it["noise"].startswith("babble") else [])
            wav = out_dir / "mixed.wav"
            write_wav(wav, apply_noise(read_wav(it["clean_wav"]), it["noise"], rng, pool))
        raw, stt_ms = run(wav)
        t = time.perf_counter()
        j = flow.finish(raw, "parakeet")
        ms = stt_ms + round((time.perf_counter() - t) * 1000)
        rss = max(rss, proc.memory_info().rss)
        kind, snr = (re.fullmatch(r"([a-z]+?)(\d*)", it["noise"]).groups())
        rows.append({"n": n, "corpus": it["corpus"], "i": it["i"], "cat": it["cat"], "split": it["split"], "gate": it["gate"],
                     "speaker": it["speaker"]["id"], "accent": it["speaker"]["accent"], "noise": it["noise"],
                     "noise_type": kind, "snr_db": int(snr) if snr else None,
                     "say": it["say"], "want_intent": it["intent"], "got_intent": j["intent"],
                     "want": it["text"], "got": j["text"], "raw": raw, "ms": ms, **score_row(it["text"], j["text"])})
        if (n + 1) % 50 == 0:
            print(f"  {n + 1}/{len(plan)} clips, running WER {wer_pct(rows)}%, p50 {pct([r['ms'] for r in rows], 50)} ms", flush=True)
    wall = time.perf_counter() - wall0
    perf = {"cpu_pct": round(100 * (sum(proc.cpu_times()[:2]) - cpu0) / wall), "ram_mb": round(rss / 2 ** 20), "decode_s": round(wall, 1)}
    return rows, perf


# ----------------------------------------------------------------------------- learning rounds

def learning_round(rows: list[dict], flow, engine: str) -> dict:
    """Round 1: held-in errors (+ stylistic decoys) go to the learner as user corrections.
    Round 2: held-out v2 rows re-scored from the cached STT text with the learned dictionary lines added.
    STT is deterministic, so re-scoring isolates the dictionary effect."""
    v2 = [r for r in rows if r["corpus"] == "v2"]
    held_in, held_out = [r for r in v2 if r["split"] == "in"], [r for r in v2 if r["split"] == "out"]
    learner, kind = load_learner(tempfile.mkdtemp())
    errs = [r for r in held_in if r["errs"]]
    for r in errs:
        learner.observe(r["got"], r["want"], "bench")
    decoy_edits, banned = make_decoys(held_in)
    for pasted, final in decoy_edits:
        for _ in range(3):  # a user who keeps making the same style edit
            learner.observe(pasted, final, "bench")
    rules_raw = list(learner.rules("bench"))
    # Never use the held-out reference sentences to select or reject rules.
    corpus_pairs = [(r["want"], r["want"]) for r in rows if r["corpus"] == "v1" or r["split"] == "in"]
    learner.guard(corpus_pairs, base=list(flow.lines))
    rules = list(learner.rules("bench"))
    base = list(flow.lines)

    def rescore(rs, lines):
        flow.lines = lines + base
        out = []
        for r in rs:
            j = flow.finish(r["raw"], "bench")
            out.append({**r, "got_after": j["text"], **{f"{k}_after": v for k, v in score_row(r["want"], j["text"]).items()}})
        flow.lines = base
        return out

    ho, hi = rescore(held_out, rules), rescore(held_in, rules)
    worse = [r for r in ho if r["errs_after"] > r["errs"]]
    better = [r for r in ho if r["errs_after"] < r["errs"]]
    pct_of = lambda rs, f: round(100 * sum(r[f] for r in rs) / max(sum(r["nref"] for r in rs), 1), 2)  # noqa: E731
    return {
        "learner": kind, "label": "real learner" if kind == "real" else "stub learner (flow_learn not importable)",
        "corrections_fed": len(errs), "decoy_edits_fed": len(decoy_edits) * 3,
        "rules_raw": rules_raw, "rules": rules, "rules_learned": len(rule_pairs(rules)), "rules_before_guard": len(rule_pairs(rules_raw)),
        "decoys_learned": decoys_learned(rules, banned),
        "heldin": {"n": len(hi), "before": pct_of(hi, "errs"), "after": pct_of(hi, "errs_after")},
        "heldout": {"n": len(ho), "before": pct_of(ho, "errs"), "after": pct_of(ho, "errs_after"),
                    "regressions": len(worse), "improved": len(better)},
        "regressed_examples": [{"want": r["want"], "before": r["got"], "after": r["got_after"]} for r in worse[:10]],
        "improved_examples": [{"want": r["want"], "before": r["got"], "after": r["got_after"]} for r in better[:5]],
    }


# ----------------------------------------------------------------------------- run

def analyse(rows: list[dict], perf: dict, plan_info: dict) -> dict:
    clean = [r for r in rows if r["noise"] == "clean"]
    noisy = [r for r in rows if r["noise"] != "clean"]
    gate = [r for r in rows if r["gate"]]
    lat = [r["ms"] for r in rows]
    return {
        "n": len(rows), "n_clean": len(clean), "n_noisy": len(noisy), "n_gate": len(gate),
        "wer_all": wer_pct(rows), "wer_clean": wer_pct(clean), "wer_noisy": wer_pct(noisy), "gate_wer": wer_pct(gate),
        "intent_acc_pct": round(100 * sum(r["got_intent"] == r["want_intent"] for r in rows) / len(rows), 1),
        "exact_match_pct": round(100 * sum(r["exact"] for r in rows) / len(rows), 1),
        "p50_ms": pct(lat, 50), "p95_ms": pct(lat, 95), **perf,
        "by_speaker": group_wer(rows, lambda r: r["speaker"]),
        "by_accent": group_wer([r for r in rows if r["speaker"].startswith("vctk")], lambda r: r["accent"]),
        "by_noise": group_wer(rows, lambda r: r["noise"]),
        "by_noise_type": group_wer(rows, lambda r: r["noise_type"]),
        "by_category": group_wer(rows, lambda r: r["cat"]),
        "top_misheard": [[list(p), n] for p, n in top_misheard([(r["want"], r["got"]) for r in rows])],
        "top_misheard_clean": [[list(p), n] for p, n in top_misheard([(r["want"], r["got"]) for r in clean])],
        **plan_info,
    }


def table(title: str, groups: dict) -> None:
    print(f"\n{title}\n  {'group':<28}{'n':>5}{'WER %':>8}{'intent':>8}{'exact':>7}")
    for k, v in groups.items():
        print(f"  {str(k):<28}{v['n']:>5}{v['wer']:>8}{v['intent_ok']:>8}{v['exact']:>7}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="v1 gate set + 1 pass over v2 (default)")
    mode.add_argument("--full", action="store_true", help="v1 gate set + 5 passes over v2")
    ap.add_argument("--local", choices=["parakeet", "stream"], default="parakeet")
    ap.add_argument("--gate", action="store_true", help="exit 1 if v1 clean SAPI WER > last ledger value + 0.5 points")
    ap.add_argument("--no-learn", action="store_true")
    ap.add_argument("--no-ledger", action="store_true", help="do not write the json or ledger row")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--passes", type=int, default=0, help="override the number of v2 passes")
    ap.add_argument("--ledger", default=str(LEDGER))
    a = ap.parse_args()
    passes = a.passes or (5 if a.full else 1)
    t_start = time.perf_counter()
    prior = last_gate_value(Path(a.ledger))

    v1, v2 = load_corpus("flow_corpus.jsonl", "v1"), load_corpus("flow_corpus_v2.jsonl", "v2")
    voices = sapi_voices()
    plan = build_plan(v1, v2, passes, voices)
    clips = CACHE / "clips"
    print(f"plan: {len(plan)} clips ({len(v1)} v1 gate + {passes} x {len(v2)} v2), voices {voices}, engine {a.local}", flush=True)
    tts_stats = synth_all(plan, clips, need_vctk=True)
    print(f"synthesis: {tts_stats['sapi_new']} new SAPI + {tts_stats['vctk_new']} new VCTK clips in {tts_stats['synth_s']} s (rest cached)", flush=True)
    import gc
    gc.collect()  # drop the TTS model before measuring RAM
    flow, run = make_engine(a.local)
    rows, perf = transcribe_all(plan, flow, run, clips, a.seed)
    shutil.rmtree(clips / "run", ignore_errors=True)
    s = analyse(rows, perf, {"tts": tts_stats})
    table("by speaker", s["by_speaker"])
    table("by accent (VCTK)", s["by_accent"])
    table("by noise condition", s["by_noise"])
    table("by category", s["by_category"])
    print("\ntop misheard, all conditions (expected -> heard x count):")
    for (e, h), n in s["top_misheard"][:15]:
        print(f"  {e or '(none)'} -> {h or '(dropped)'}  x{n}")
    print("\ntop misheard, clean audio only:")
    for (e, h), n in s["top_misheard_clean"][:15]:
        print(f"  {e or '(none)'} -> {h or '(dropped)'}  x{n}")

    learning = None if a.no_learn else learning_round(rows, flow, a.local)
    if learning:
        print(f"\nlearning ({learning['label']}): fed {learning['corrections_fed']} corrections + {learning['decoy_edits_fed']} decoy edits; "
              f"rules {learning['rules_before_guard']} -> {learning['rules_learned']} after guard; decoys learned {learning['decoys_learned']}")
        print(f"  held-out WER {learning['heldout']['before']}% -> {learning['heldout']['after']}%  "
              f"(improved {learning['heldout']['improved']}, regressions {learning['heldout']['regressions']}; "
              f"held-in {learning['heldin']['before']}% -> {learning['heldin']['after']}%)")
        for ln in learning["rules"][:20]:
            print(f"  rule: {ln}")

    voices_n = len({r["speaker"] for r in rows})
    res = {"meta": {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "git_sha": git_sha(), "engine": a.local,
                    "corpus": f"v1 gate {len(v1)} + v2 {len(v2)} x{passes}",
                    "conditions": f"{voices_n} voices x {len(NOISES)} noise; n={len(rows)}",
                    "mode": "full" if a.full else "quick", "labels": "SYNTHETIC speech and noise; no other product measured",
                    "run_s": round(time.perf_counter() - t_start)},
           "summary": s, "learning": learning, "rows": rows}
    gate_now = s["gate_wer"]
    print(f"\nSUMMARY n={s['n']} WER clean {s['wer_clean']}% noisy {s['wer_noisy']}% all {s['wer_all']}% | intent {s['intent_acc_pct']}% "
          f"exact {s['exact_match_pct']}% | p50 {s['p50_ms']} ms p95 {s['p95_ms']} ms | CPU {s['cpu_pct']}% RAM {s['ram_mb']} MB | "
          f"gate (v1 clean SAPI) {gate_now}% vs last {prior}")
    if not a.no_ledger:
        out = EVIDENCE / "flow-loop" / (datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{res['meta']['mode']}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        append_ledger(Path(a.ledger), ledger_row(res))
        print(f"wrote {out} and a ledger row")
    print(f"run time {round(time.perf_counter() - t_start)} s")
    if a.gate:
        if prior is None:
            print("GATE: no earlier ledger value, nothing to compare; pass")
        elif gate_now is not None and gate_now - prior > GATE_TOLERANCE_PTS:
            print(f"GATE FAIL: v1 clean SAPI WER {gate_now}% is {gate_now - prior:.2f} points above the last ledger value {prior}%")
            return 1
        else:
            print(f"GATE ok: {gate_now}% vs {prior}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
