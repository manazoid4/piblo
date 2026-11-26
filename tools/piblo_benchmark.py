#!/usr/bin/env python3
"""Latency benchmark against the physical Piblo via Core: piblo_benchmark.py [-n 20] [--no-device]

Measures (median / p95 ms): status round-trip, screen PNG capture, key->screen-change (opens settings,
presses down/up, polls the screen until pixels change, then Esc back to Home; never presses Enter),
and the in-process Core command router on a few phrases, counting any LLM (HTTP) call it makes.
Writes JSON to tools/bench/<timestamp>.json. Items needing a device are skipped with --no-device.
"""
import argparse
import json
import pathlib
import statistics
import sys
import time

import _common as c

HERE = pathlib.Path(__file__).resolve().parent
PHRASES = ["volume up", "what's next", "open settings", "remind me in 5 minutes to stretch", "lock the pc"]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def summarize(xs):
    return {"n": len(xs), "median_ms": round(statistics.median(xs), 1), "p95_ms": round(pct(xs, 95), 1)} if xs else None


def timed(fn, n):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return out


def key_to_change(n, timeout=3.0):
    c.open_app("settings")
    time.sleep(1.0)
    out, keys = [], ["down", "up"]
    try:
        for i in range(n):
            before = c.png_bytes()
            t = time.perf_counter()
            c.send_keys(keys[i % 2])
            while time.perf_counter() - t < timeout:
                if c.png_bytes() != before:
                    out.append((time.perf_counter() - t) * 1000)
                    break
    finally:
        c.send_keys("esc")
    return out


def router_bench():
    """Time CommandRouter.route in-process; a fake HTTP client counts LLM calls (tier 1 fallback)."""
    sys.path.insert(0, str(HERE.parents[0] / "host"))
    from mazhost.command_router import CommandRouter

    class Counting:
        calls = 0

        def post(self, *a, **k):
            Counting.calls += 1
            raise RuntimeError("no model in benchmark")

    r = CommandRouter(ollama_url="http://127.0.0.1:0", model="none", client=Counting())
    res = {}
    for ph in PHRASES:
        Counting.calls = 0
        ms = timed(lambda: r.route(ph, model=False), 200)
        det = r.route(ph, model=False)
        n0 = Counting.calls
        full = r.route(ph, model=True)
        res[ph] = {"deterministic_tier": det.tier, "capability": det.capability, "median_ms_model_false": round(statistics.median(ms), 3),
                   "llm_calls_with_model_true": Counting.calls - n0, "tier_with_model_true": full.tier}
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--no-device", action="store_true")
    a = ap.parse_args(argv)
    result = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "n": a.n, "measured_on": "device" if not a.no_device else "none"}
    if not a.no_device:
        c.need_token()
        s = c.status()
        if not s:
            sys.exit("error: no device via Core (use --no-device for router-only)")
        result["device_version"] = s.get("version")
        result["status_roundtrip"] = summarize(timed(c.status, a.n))
        result["screen_capture"] = summarize(timed(c.png_bytes, a.n))
        result["key_to_screen_change"] = summarize(key_to_change(a.n))
    try:
        result["router"] = router_bench()
    except Exception as e:  # router needs host deps installed; report, don't fail the device numbers
        result["router"] = {"error": str(e)[:200]}
    out = HERE / "bench"
    out.mkdir(exist_ok=True)
    path = out / f"{c.stamp()}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(path)


if __name__ == "__main__":
    main()
