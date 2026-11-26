#!/usr/bin/env python3
"""Discover Piblo and serve a live LCD preview in the browser: piblo_live.py --auto [--port 8799]

Prints one status line (serial ports by Espressif VID 303A, Core status, device IP), then serves
http://127.0.0.1:<port>/ (loopback only). The page polls Core's existing screen PNG at <= 2 fps and is
labelled "PHYSICAL DEVICE via Core" when a device answers, else "SIMULATED" (no device; blank frame).
No new firmware streaming. The browser never sees the token: this server proxies /frame.png.
"""
import argparse
import http.server
import json

import _common as c
from visual_diff import encode_rgb

PAGE = """<!doctype html><meta charset=utf-8><title>Piblo live</title>
<body style="background:#111;color:#eee;font:16px system-ui;text-align:center">
<h3 id=l>...</h3><img id=i style="image-rendering:pixelated;width:720px;max-width:95vw;border:2px solid #444">
<script>
async function t(){try{const s=await (await fetch('/state')).json();
l.textContent=s.live?'PHYSICAL DEVICE via Core  v'+s.version+'  app='+s.app:'SIMULATED (no device)';
l.style.color=s.live?'#4f4':'#fa0';i.src='/frame.png?'+Date.now();}catch(e){l.textContent='server gone'}}
t();setInterval(t,600)</script>"""

BLANK = encode_rgb(240, 135, bytes(240 * 135 * 3))  # black frame shown while simulated


def summary():
    s = c.status()
    ports = ",".join(c.esp_ports()) or "none"
    if not s:
        return f"device=OFFLINE usb={ports} core={c.pr.CORE}", None
    return (f"device=ONLINE v{s.get('version')} app={s.get('app')} ip={s.get('ip')} "
            f"core_link={s.get('host_link')} usb={ports} core={c.pr.CORE}"), s


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send(PAGE.encode(), "text/html")
        elif path == "/state":
            s = c.status()
            self._send(json.dumps({"live": bool(s), "version": (s or {}).get("version"),
                                   "app": (s or {}).get("app")}).encode(), "application/json")
        elif path == "/frame.png":
            try:
                self._send(c.png_bytes(), "image/png")
            except RuntimeError:
                self._send(BLANK, "image/png")
        else:
            self._send(b"not found", "text/plain", 404)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--auto", action="store_true", help="discover and serve (default behaviour)")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--no-serve", action="store_true", help="print the status line and exit")
    a = ap.parse_args(argv)
    c.need_token()
    line, _ = summary()
    print(line)
    if a.no_serve:
        return
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"preview: http://127.0.0.1:{a.port}/  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
