"""Stage a built firmware for Core: python scripts/fw_manifest.py [bin] [out_dir]

Copies the bin to <out_dir>/latest.bin and writes manifest.json {version, sha, sha256, size}.
version/sha are read from the bin itself (the About string), so they always match what the device runs.
"""
import hashlib, json, re, shutil, sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / ".pio/build/cardputer-adv/firmware.bin"
out = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "host/fw"
data = src.read_bytes()
m = re.search(rb"(?:nod|Piblo) v(\d+\.\d+(?:\.\d+)?) \(([0-9a-f]+|nogit)\)\nCore", data)
if not m:
    sys.exit("no nod version string in bin")
out.mkdir(parents=True, exist_ok=True)
shutil.copyfile(src, out / "latest.bin")
manifest = {"version": m[1].decode(), "sha": m[2].decode(),
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
(out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
print(manifest)
