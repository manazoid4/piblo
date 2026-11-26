#!/usr/bin/env python3
"""Benchmark real task completion through Piblo Core's assist pipeline.

Measures what the user feels: time to the "On it" acknowledgement, time to the
final answer, and whether the task actually completed, for the everyday tasks
(find my device, ring it, where was I, sort this out, plus an unknown request).
Not model tokens per second.

    python scripts/benchmark_assist.py                 # against the running Core (finds it like piblo_remote)
    python scripts/benchmark_assist.py --inprocess     # a throwaway Core in this process (CI / no PC)
    python scripts/benchmark_assist.py --rounds 10 --json out.json

Against a real Core it only reads and rings nothing: tasks that would act
(ring, fixes) are skipped unless --allow-ring is given. The token is never printed.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

TASKS = [
    # (name, utterance, expected capability, expected final state, acts?)
    ("find_device", "Piblo, find my phone", "devices.find", "done", False),
    ("ring_followup", "ring it", "devices.ring", "done", True),
    ("where_was_i", "where was I?", "resume.packet", "done", False),
    ("sort_this_out", "sort this out", "troubleshoot.diagnose", "done", False),
    ("unknown", "book a table for two at eight", None, "unclear", False),
]


def pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 1)


class HttpCore:
    def __init__(self):
        import piblo_remote as pr  # standard-library discovery of Core URL + token
        if not pr.TOKEN:
            raise SystemExit("No Piblo token found. Open the Piblo app, or set PIBLO_TOKEN.")
        self.pr = pr

    def post(self, path, body):
        status, raw = self.pr.call(path, "POST", body)
        return status, json.loads(raw or b"{}")


class InProcessCore:
    def __init__(self):
        sys.path.insert(0, str(REPO / "host"))
        import os
        home = tempfile.mkdtemp(prefix="piblo-bench-")
        os.environ.update({"HOME": home, "USERPROFILE": home, "MAZ_DATA_DIR": f"{home}/data",
                           "MAZ_BUDDY_DB": f"{home}/buddy.db", "MAZ_WAKEWORD": "0"})
        from fastapi.testclient import TestClient
        from mazhost.app import create_app
        from mazhost.config import Settings
        token = "bench-token-not-default"
        settings = Settings(token=token, _env_file=None, control_dir=f"{home}/control",
                            project_roots=tempfile.mkdtemp())
        self.c = TestClient(create_app(settings))
        self.h = {"Authorization": f"Bearer {token}"}
        self.c.post("/finddevices/devices", headers=self.h, json={
            "id": "phone", "name": "Pixel phone", "kind": "phone", "aliases": ["phone"],
            "providers": {"piblo_phone": {}}})
        self.c.post("/finddevices/sightings", headers=self.h, json={
            "observer": "bench", "sightings": [{"device_id": "phone", "rssi": -48}]})
        self.c.post("/buddy/task", headers=self.h, json={"id": "bench", "title": "Benchmark task",
                                                         "status": "running", "agent": "PibloCraft"})

    def post(self, path, body):
        r = self.c.post(path, headers=self.h, json=body)
        return r.status_code, r.json()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--inprocess", action="store_true")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--allow-ring", action="store_true", help="really ring the phone on a live Core")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)
    core = InProcessCore() if args.inprocess else HttpCore()
    allow_acts = args.inprocess or args.allow_ring
    rows = []
    for rnd in range(args.rounds):
        session = f"bench-{int(time.time())}-{rnd}"
        for name, text, cap, want, acts in TASKS:
            if acts and not allow_acts:
                continue
            started = time.perf_counter()
            status, body = core.post("/assist", {"text": text, "session": session, "source": "bench", "wait": 20})
            wall = (time.perf_counter() - started) * 1000
            run = body.get("run", {})
            ack = next((e["at_ms"] for e in run.get("events", []) if e["type"] == "assist.ack"), None)
            ok = status == 200 and run.get("state") == want and (cap is None or run.get("capability") == cap)
            rows.append({"task": name, "ok": ok, "state": run.get("state"), "capability": run.get("capability"),
                         "tier": run.get("tier"), "ack_ms": ack, "pipeline_ms": run.get("timings", {}).get("total_ms"),
                         "http_wall_ms": round(wall, 1), "reply": (run.get("reply") or "")[:90]})
    report = {"mode": "inprocess" if args.inprocess else "live", "rounds": args.rounds, "tasks": {}}
    for name in dict.fromkeys(r["task"] for r in rows):
        rs = [r for r in rows if r["task"] == name]
        report["tasks"][name] = {
            "success_rate": round(sum(r["ok"] for r in rs) / len(rs), 3),
            "ack_ms_p50": pct([r["ack_ms"] for r in rs if r["ack_ms"] is not None], 0.5),
            "pipeline_ms_p50": pct([r["pipeline_ms"] for r in rs if r["pipeline_ms"] is not None], 0.5),
            "pipeline_ms_p95": pct([r["pipeline_ms"] for r in rs if r["pipeline_ms"] is not None], 0.95),
            "http_wall_ms_p50": pct([r["http_wall_ms"] for r in rs], 0.5),
            "tier": rs[0]["tier"], "example_reply": rs[0]["reply"],
            "errors": [r["state"] for r in rs if not r["ok"]][:3],
        }
    report["overall_success_rate"] = round(sum(r["ok"] for r in rows) / len(rows), 3) if rows else None
    out = json.dumps(report, indent=2)
    print(out)
    if args.json:
        Path(args.json).write_text(out, encoding="utf-8")
    return 0 if report["overall_success_rate"] == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
