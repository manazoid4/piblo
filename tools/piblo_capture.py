#!/usr/bin/env python3
"""Save a PNG of the physical Piblo LCD (via Core): piblo_capture.py --screen current|<app> [--out dir]

current: capture whatever is showing. <app> (e.g. settings): opens it, captures, then Esc returns home.
File name: <timestamp>-<app>.png. Reuses scripts/piblo_remote.py (Core URL/token, 3x scaling).
"""
import argparse
import pathlib
import time

import _common as c


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--screen", default="current", help="'current' or an app id such as settings")
    ap.add_argument("--out", default="tools/captures", help="output directory")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--settle", type=float, default=0.8, help="seconds to wait after opening the app")
    a = ap.parse_args(argv)
    c.need_token()
    d = pathlib.Path(a.out)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{c.stamp()}-{a.screen}.png"
    if a.screen != "current":
        c.open_app(a.screen)
        time.sleep(a.settle)
    try:
        c.pr.screen(str(path), max(1, a.scale))
    finally:
        if a.screen != "current":
            c.send_keys("esc")
    print(path)


if __name__ == "__main__":
    main()
