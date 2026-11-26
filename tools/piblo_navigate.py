#!/usr/bin/env python3
"""Drive the physical Piblo through Core: piblo_navigate.py --screen <app> | --keys "down*2 enter"

--screen opens an app (settings, ...). --keys sends a key spec (Core parses "down*2 enter", esc, letters).
Prints app + version afterwards. Never sends update/reboot actions; piblo_flash.py owns those.
"""
import argparse
import sys
import time

import _common as c


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--screen", help="app id to open")
    g.add_argument("--keys", help='key spec, e.g. "down*2 enter"')
    a = ap.parse_args(argv)
    c.need_token()
    try:
        c.open_app(a.screen) if a.screen else c.send_keys(a.keys)
    except RuntimeError as e:
        sys.exit(f"error: {e}")
    time.sleep(0.5)
    s = c.status() or {}
    print(f"app={s.get('app')} v{s.get('version')}")


if __name__ == "__main__":
    main()
