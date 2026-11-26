#!/usr/bin/env python3
"""Pixel diff of two PNGs (stdlib only): prints % of differing pixels, writes a diff PNG.

    python tools/visual_diff.py a.png b.png [--out diff.png] [--tol 0]

Differing pixels are red on a dimmed copy of b. It reports, it does not judge (exit 0).
Decodes 8-bit grey/RGB/RGBA non-interlaced PNGs (what Piblo screenshots are).
"""
import argparse
import struct
import sys
import zlib

CH = {0: 1, 2: 3, 4: 2, 6: 4}


def decode_png(data):
    """-> (w, h, channels, bytearray of all rows)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, hdr = 8, b"", None
    while pos < len(data):
        n, t = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if t == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif t == b"IDAT":
            idat += body
    w, h, depth, ctype, _, _, inter = hdr
    if depth != 8 or ctype not in CH or inter:
        raise ValueError("only 8-bit non-interlaced grey/RGB/RGBA PNG supported")
    c = CH[ctype]
    stride = w * c
    raw, out = zlib.decompress(idat), bytearray()
    prev = bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - c] if i >= c else 0
            b = prev[i]
            cc = prev[i - c] if i >= c else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - cc
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - cc)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else cc)) & 255
        out += line
        prev = line
    return w, h, c, out


def encode_rgb(w, h, rgb):
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    rows = b"".join(b"\x00" + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def _rgb(px, c, i):
    p = px[i * c:i * c + c]
    return p[:1] * 3 if c <= 2 else p[:3]


def diff(a_png, b_png, tol=0):
    """-> (percent_changed, diff_png_bytes). Sizes must match."""
    wa, ha, ca, pa = decode_png(a_png)
    wb, hb, cb, pb = decode_png(b_png)
    if (wa, ha) != (wb, hb):
        raise ValueError(f"size mismatch {wa}x{ha} vs {wb}x{hb}")
    out, changed = bytearray(), 0
    for i in range(wa * ha):
        x, y = _rgb(pa, ca, i), _rgb(pb, cb, i)
        if max(abs(p - q) for p, q in zip(x, y)) > tol:
            changed += 1
            out += b"\xff\x00\x00"
        else:
            out += bytes(v // 3 for v in y)
    return 100.0 * changed / (wa * ha), encode_rgb(wa, ha, out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--out", default="diff.png")
    ap.add_argument("--tol", type=int, default=0, help="per-channel tolerance (0-255)")
    a = ap.parse_args(argv)
    try:
        with open(a.a, "rb") as fa, open(a.b, "rb") as fb:
            pct, png = diff(fa.read(), fb.read(), a.tol)
    except (OSError, ValueError) as e:
        sys.exit(f"error: {e}")
    with open(a.out, "wb") as f:
        f.write(png)
    print(f"{pct:.2f}% pixels differ -> {a.out}")


if __name__ == "__main__":
    main()
