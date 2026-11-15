"""Parse Piblo benchmark serial logs ("[health]" + "[bench]" lines) and compare runs.

  python scripts/bench_parse.py baseline.log                 # summary table
  python scripts/bench_parse.py baseline.log candidate.log   # + delta column (candidate vs baseline)
  python scripts/bench_parse.py run.log --json               # machine-readable

Lines it reads (printed by firmware env cardputer-adv-bench every 30 s):
  [health] heap=.. min=.. largest=.. stack=.. loop_max=..ms pressure=.. stall=..
  [bench] t=.. boot_ms=.. loop_ms=n/min/mean/max/over net_ms=.. voice_ms=.. audio_gaps=N
over = samples over the limit (loop: >100 ms). audio_gaps is a PROXY (loop >100 ms while the
mic/speaker was live), not a true underrun count. Nothing here talks to a serial port: capture
the log yourself (see docs in the PR) and feed the file in.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

STAT = re.compile(r"^(\d+)/(\d+)/(\d+)/(\d+)/(\d+)$")
KV = re.compile(r"(\w+)=(\S+)")


def merge(acc: dict, key: str, n: int, lo: int, mean: int, hi: int, over: int) -> None:
    if n == 0:
        return
    a = acc.setdefault(key, {"count": 0, "min": lo, "max": hi, "sum": 0, "over": 0})
    a["count"] += n
    a["min"] = min(a["min"], lo)
    a["max"] = max(a["max"], hi)
    a["sum"] += mean * n
    a["over"] += over


def parse(text: str) -> dict:
    stats: dict = {}
    scalars: dict = {"min_heap": None, "min_largest": None, "min_stack": None,
                     "boot_ms": None, "audio_gaps": 0, "health_samples": 0, "bench_samples": 0}
    for line in text.splitlines():
        if "[health]" in line:
            kv = dict(KV.findall(line))
            scalars["health_samples"] += 1
            for src, dst in (("min", "min_heap"), ("largest", "min_largest"), ("stack", "min_stack")):
                v = int(kv.get(src, "0").rstrip("ms") or 0)
                if v and (scalars[dst] is None or v < scalars[dst]):
                    scalars[dst] = v
            lm = int(kv.get("loop_max", "0ms").rstrip("ms") or 0)
            merge(stats, "health_loop_max_ms", 1, lm, lm, lm, 0)
        elif "[bench]" in line:
            kv = dict(KV.findall(line))
            scalars["bench_samples"] += 1
            if scalars["boot_ms"] is None and "boot_ms" in kv:
                scalars["boot_ms"] = int(kv["boot_ms"])
            scalars["audio_gaps"] += int(kv.get("audio_gaps", "0"))
            for name in ("loop_ms", "net_ms", "voice_ms"):
                m = STAT.match(kv.get(name, ""))
                if m:
                    merge(stats, name, *map(int, m.groups()))
    for a in stats.values():
        a["mean"] = round(a["sum"] / a["count"]) if a["count"] else 0
        del a["sum"]
    return {"stats": stats, **scalars}


def rows(res: dict) -> dict:
    out = {}
    for name, a in res["stats"].items():
        out[f"{name}.mean"], out[f"{name}.max"], out[f"{name}.over"] = a["mean"], a["max"], a["over"]
    for k in ("boot_ms", "audio_gaps", "min_heap", "min_largest", "min_stack"):
        out[k] = res[k]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("candidate", nargs="?")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    base = parse(open(a.base, encoding="utf-8", errors="replace").read())
    cand = parse(open(a.candidate, encoding="utf-8", errors="replace").read()) if a.candidate else None
    if a.json:
        print(json.dumps({"base": base, "candidate": cand}, indent=2))
        return 0
    b = rows(base)
    c = rows(cand) if cand else {}
    print(f"{'metric':24}{'baseline':>12}" + (f"{'candidate':>12}{'delta':>10}" if cand else ""))
    for k, v in b.items():
        line = f"{k:24}{str(v):>12}"
        if cand:
            cv = c.get(k)
            d = "" if v is None or cv is None else f"{cv - v:+d}"
            line += f"{str(cv):>12}{d:>10}"
        print(line)
    if not base["bench_samples"]:
        print("no [bench] lines found: was this the cardputer-adv-bench build?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
