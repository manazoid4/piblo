import asyncio
import sqlite3
import tempfile
import time
from pathlib import Path

from mazhost.resume import CHROMIUM_EPOCH_OFFSET_S, Resumer, install_resume, recent_pages
from mazhost.universal_registry import EventBus, UniversalCapabilityRegistry

NOW = 1_800_000_000.0


def make(tasks=(), where=(), pages=(), browser=False, roots=None):
    opened = []
    r = Resumer(tasks=lambda: list(tasks), where=lambda: list(where), pages=lambda: list(pages),
                browser_allowed=lambda: browser, open_url=lambda u: opened.append(u) or True,
                open_path=lambda p: opened.append(p) or True, project_roots=roots or [], clock=lambda: NOW)
    return r, opened


def test_prefers_unfinished_agent_task_and_says_it():
    tasks = [{"id": "t2", "title": "Write docs", "status": "done", "agent": "craft", "updated": NOW - 10},
             {"id": "t1", "title": "Fix login bug", "status": "running", "agent": "PibloCraft",
              "progress": 60, "detail": "tests failing in auth", "updated": NOW - 600}]
    r, _ = make(tasks=tasks)
    p = r.packet()
    assert p["task"]["id"] == "t1"
    assert "PibloCraft was working on Fix login bug (60%)" in p["reply"]
    assert p["next"]["capability"] == "resume.continue"


def test_old_things_are_ignored_and_empty_is_honest():
    r, _ = make(tasks=[{"id": "x", "title": "Ancient", "status": "running", "updated": NOW - 30 * 86400}])
    p = r.packet()
    assert p["empty"] and "can't find anything recent" in p["reply"]
    assert "next" not in p


def test_browser_history_needs_permission():
    pages = [{"url": "https://example.com/a", "title": "Spec", "at": NOW - 5}]
    r, _ = make(pages=pages, browser=False)
    assert r.packet()["browser_history"] == "not_allowed" and r.packet()["pages"] == []
    r, _ = make(pages=pages, browser=True)
    assert r.packet()["pages"][0]["url"] == "https://example.com/a"


def test_continue_reopens_doc_and_page_and_publishes_handoff():
    root = Path(tempfile.mkdtemp())
    (root / "notes.md").write_text("x")
    where = [{"id": "doc-1", "kind": "document", "title": "notes.md", "when": NOW - 30, "source": "project:notes.md"},
             {"id": "park-1", "kind": "park", "title": "call the bank", "when": NOW - 60, "source": "dumps"}]
    tasks = [{"id": "t1", "title": "Fix login bug", "status": "failed", "agent": "PibloCraft", "updated": NOW - 60}]
    r, opened = make(tasks=tasks, where=where, pages=[{"url": "https://x.test/p", "title": "P", "at": NOW}],
                     browser=True, roots=[root])
    reg = UniversalCapabilityRegistry(EventBus())
    seen = []
    reg.event_bus.subscribe("craft.resume.requested", lambda ev: seen.append(ev.payload))
    install_resume(reg, resumer=r)
    out = asyncio.run(reg.execute("resume.continue", {}))
    assert out["success"]
    assert str((root / "notes.md").resolve()) in opened and "https://x.test/p" in opened
    assert seen[0]["task_id"] == "t1" and seen[0]["context"]["parked"] == "call the bank"


def test_doc_path_cannot_escape_project_root():
    root = Path(tempfile.mkdtemp())
    r, opened = make(where=[{"id": "d", "kind": "document", "title": "x", "when": NOW,
                             "source": "project:../../etc/passwd"}], roots=[root])
    r.continue_()
    assert opened == []


def test_recent_pages_reads_chromium_history_copy():
    folder = Path(tempfile.mkdtemp())
    db = folder / "History"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, last_visit_time INTEGER)")
    now = time.time()
    to_chrome = lambda t: int((t + CHROMIUM_EPOCH_OFFSET_S) * 1_000_000)
    con.executemany("INSERT INTO urls(url,title,last_visit_time) VALUES (?,?,?)", [
        ("https://new.test/", "New", to_chrome(now - 60)),
        ("chrome://settings", "Settings", to_chrome(now - 30)),
        ("https://old.test/", "Old", to_chrome(now - 30 * 86400)),
    ])
    con.commit()
    con.close()
    pages = recent_pages([db], now=now)
    assert [p["url"] for p in pages] == ["https://new.test/"]
