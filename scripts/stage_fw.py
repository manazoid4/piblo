"""Stage the CURRENT main firmware for Core's Update button; refuses anything else.

  python scripts/stage_fw.py [out_dir]       download main's latest green build, verify, stage to out_dir (default host/fw)
  python scripts/stage_fw.py --bin X [out]   stage a local bin, but only if its embedded sha is origin/main's HEAD

Guards (why: on 7 Nov Core served an old d88e714 build that a hand copy left in host/fw):
build must come from a successful "Piblo build" run on main whose head sha == origin/main now,
the bin's embedded sha must equal that head, first byte 0xE9, size under the 1,507,328 B app slot.
Needs git + gh on the machine that runs it; copy the staged folder to the PC afterwards.
"""
import json, re, subprocess, sys, tempfile
from pathlib import Path

LIMIT = 1507328
root = Path(__file__).resolve().parents[1]
sh = lambda *a: subprocess.run(a, check=True, capture_output=True, text=True, cwd=root).stdout.strip()


def main_head():
    sh("git", "fetch", "-q", "origin", "main")
    return sh("git", "rev-parse", "origin/main")


def check(binpath, head):
    d = Path(binpath).read_bytes()
    m = re.search(rb"(?:nod|Piblo) v(\d+\.\d+(?:\.\d+)?) \(([0-9a-f]+)\)\nCore", d)
    if not m: sys.exit("REFUSED: no nod version string in bin")
    if not head.startswith(m[2].decode()): sys.exit(f"REFUSED: bin is build {m[2].decode()}, origin/main is {head[:7]}")
    if d[0] != 0xE9: sys.exit("REFUSED: not an ESP32 app image (first byte != 0xE9)")
    if len(d) >= LIMIT: sys.exit(f"REFUSED: {len(d)} B does not fit the {LIMIT} B slot")


def main():
    a = sys.argv[1:]
    head = main_head()
    if a[:1] == ["--bin"]:
        binpath, out = a[1], (a[2:] or ["host/fw"])[0]
    else:
        out = (a or ["host/fw"])[0]
        runs = json.loads(sh("gh", "run", "list", "-R", "manazoid4/piblo", "--workflow", "firmware.yml", "--branch", "main",
                             "--status", "success", "-L", "1", "--json", "databaseId,headSha"))
        if not runs or runs[0]["headSha"] != head:
            sys.exit(f"REFUSED: latest green main build is {runs and runs[0]['headSha'][:7]}, origin/main is {head[:7]} (CI still running?)")
        tmp = tempfile.mkdtemp(prefix="nodfw-")
        sh("gh", "run", "download", str(runs[0]["databaseId"]), "-R", "manazoid4/piblo", "-D", tmp)
        binpath = next(Path(tmp).rglob("*M5Launcher.bin"))
    check(binpath, head)
    sh(sys.executable, "scripts/fw_manifest.py", str(binpath), out)
    print(f"staged main {head[:7]} -> {out}\n" + (Path(out) / "manifest.json").read_text())


main()
