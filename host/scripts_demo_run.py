"""Record the two homepage demos against the real Core app (not the live Core process).

Run from host/:  python scripts_demo_run.py <out_dir>
Uses the real HTTP routes the handheld calls (/session/start, /turn/text, /buddy/summary,
/buddy/decide), the real PC controller, real Promise Keeper and real Focus. Data goes to a temp dir.
Demo 1 needs OpenOffice Calc; it creates Budget.ods, types into it, then asks Piblo to save it."""
from __future__ import annotations

import json, os, sys, tempfile, time, zipfile, subprocess
from pathlib import Path

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="piblo-demo-"))
os.environ["MAZ_NOW_FILE"] = str(TMP / "now.md"); os.environ["MAZ_FOCUS_LOG"] = str(TMP / "focus.log")
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, str(Path(__file__).parent / "tests"))

from fastapi.testclient import TestClient                      # noqa: E402
from PIL import ImageGrab                                        # noqa: E402
from mazhost.app import create_app                               # noqa: E402
from mazhost.config import Settings                              # noqa: E402
from mazhost.pc import PCController                              # noqa: E402
from mazhost.flow_routes import foreground_window                # noqa: E402
from test_app import FakeBeam, FakeModels, FakeNudge, FakeStt, FakeTelemetry  # noqa: E402

TOKEN = "demo-token-that-is-not-default"
H = {"Authorization": f"Bearer {TOKEN}"}
log: list[dict] = []


def note(event: str, **data):
    row = {"t": time.strftime("%H:%M:%S"), "event": event, **data}
    log.append(row); print(json.dumps(row, ensure_ascii=False))


def make_ods(path: Path) -> None:
    content = ('<?xml version="1.0" encoding="UTF-8"?><office:document-content '
               'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
               'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">'
               '<office:body><office:spreadsheet><table:table table:name="November">'
               + "".join(f'<table:table-row><table:table-cell office:value-type="string"><text:p>{a}</text:p></table:table-cell>'
                         f'<table:table-cell office:value-type="float" office:value="{b}"><text:p>{b}</text:p></table:table-cell></table:table-row>'
                         for a, b in (("Rent", 850), ("Food", 220), ("Travel", 96)))
               + '</table:table></office:spreadsheet></office:body></office:document-content>')
    manifest = ('<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", manifest); z.writestr("content.xml", content)


def type_text(s: str) -> None:
    import ctypes
    u = ctypes.windll.user32
    for ch in s:
        if ch == "\n":
            u.keybd_event(0x0D, 0, 0, 0); u.keybd_event(0x0D, 0, 2, 0)
        else:
            for flag in (0x0004, 0x0004 | 0x0002):   # KEYEVENTF_UNICODE down/up
                inp = (ctypes.c_ulong * 7)(1, 0, 0, 0, 0, 0, 0)
                ki = ctypes.c_ushort * 4
                class KI(ctypes.Structure): _fields_ = [("vk", ctypes.c_ushort), ("scan", ctypes.c_ushort), ("flags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("extra", ctypes.c_size_t)]
                class U(ctypes.Union): _fields_ = [("ki", KI), ("pad", ctypes.c_byte * 32)]
                class INPUT(ctypes.Structure): _fields_ = [("type", ctypes.c_ulong), ("u", U)]
                i = INPUT(1, U(ki=KI(0, ord(ch), flag, 0, 0)))
                u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))
        time.sleep(0.02)


settings = Settings(token=TOKEN, _env_file=None, data_dir=str(TMP / "data"), buddy_db=str(TMP / "buddy.sqlite3"),
                    work_dir=str(TMP / "work"), control_dir=str(TMP / "control"), debug_dir=str(TMP / "debug"),
                    project_roots=str(TMP))
client = TestClient(create_app(settings, stt=FakeStt(), models=FakeModels(), nudge=FakeNudge(), pc=PCController(),
                               beam=FakeBeam(), telemetry=FakeTelemetry()))
sid = client.post("/session/start", headers=H).json()["session_id"]

# ---------------------------------------------------------------- demo 1
which = sys.argv[2] if len(sys.argv) > 2 else "both"
if which in ("both", "1"):
    sheet = TMP / "Budget.ods"   # never touch a real Documents file
    make_ods(sheet)
    calc = r"C:\Program Files (x86)\OpenOffice 4\program\scalc.exe"
    subprocess.Popen([calc, "-nologo", str(sheet)])
    for _ in range(60):
        time.sleep(1)
        if "Budget" in foreground_window()["title"]:
            break
    time.sleep(2)
    note("window", title=foreground_window()["title"])
    if "Budget" not in foreground_window()["title"]:
        sys.exit("Budget.ods is not in front; not typing into another window")
    type_text("Phone\n42\n")                    # an unsaved edit, like real work in progress
    time.sleep(1)
    before = sheet.stat().st_mtime
    ImageGrab.grab().save(OUT / "demo1-before.png")
    said = "save the spreadsheet I'm working on and remind me to finish it tonight"
    r = client.post("/turn/text", headers=H, json={"text": said, "session_id": sid, "route": "local"}).json()
    note("turn", said=said, reply=r.get("reply"), provider=r.get("provider"))
    summary = client.get("/buddy/summary", headers=H).json()
    card = summary["items"][0] if summary["items"] else {}
    note("device_card", tool=card.get("tool"), summary=card.get("summary"), left_s=card.get("left"))
    time.sleep(1.5)
    d = client.post("/buddy/decide", headers=H, json={"id": card["id"], "decision": "allow"})
    note("yes_pressed", status=d.status_code)
    for _ in range(40):
        last = client.get("/compound/last", headers=H).json()
        if last.get("state") in ("done", "failed"):
            break
        time.sleep(0.25)
    note("result", state=last.get("state"), results=last.get("results"))
    time.sleep(1.5)
    after = sheet.stat().st_mtime
    note("file_check", path=sheet.name, saved=after > before, mtime_before=round(before), mtime_after=round(after))
    with zipfile.ZipFile(sheet) as z:
        note("file_contains_edit", phone_row="Phone" in z.read("content.xml").decode("utf-8", "ignore"))
    ImageGrab.grab().save(OUT / "demo1-after.png")
    note("promises_today", today=client.get("/promises/today", headers=H).json())

# ---------------------------------------------------------------- demo 2
if which in ("both", "2"):
    said = "start a 45 minute focus session and remind me to call Sam at 5pm"
    r = client.post("/turn/text", headers=H, json={"text": said, "session_id": sid, "route": "local"}).json()
    note("turn", said=said, reply=r.get("reply"), provider=r.get("provider"))
    note("approval_needed", pending=client.get("/buddy/summary", headers=H).json()["count"])
    note("focus_state", focus=client.get("/focus/state", headers=H).json())
    note("promises_today", today=client.get("/promises/today", headers=H).json())

(OUT / "demo-run.json").write_text(json.dumps(log, indent=1, ensure_ascii=False), encoding="utf-8")
print("saved", OUT / "demo-run.json")
