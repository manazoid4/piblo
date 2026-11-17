from datetime import date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost.ledger import append_ledger, build_ledger_router, read_ledger, search_ledger


def test_append_read_search(tmp_path):
    now = datetime(2026, 11, 16, 9, 5)
    append_ledger("note", "call Sam\n## fake heading", root=tmp_path, now=now)
    append_ledger("dictation", "buy milk", root=tmp_path, now=now)
    text = read_ledger("2026-11-16", root=tmp_path)
    assert text.startswith("# Piblo ledger 2026-11-16")
    assert "\n## fake" not in text and "## 09:05 dictation" in text
    hits = search_ledger("MILK", days=3, root=tmp_path, today=date(2026, 11, 17))
    assert hits == [{"date": "2026-11-16", "line": "buy milk"}]
    assert search_ledger("milk", days=1, root=tmp_path, today=date(2026, 11, 18)) == []


def test_validation(tmp_path):
    for bad in (("bad kind", "x"), ("note", "  "), ("note", "x" * 20001)):
        try:
            append_ledger(*bad, root=tmp_path)
        except ValueError:
            continue
        raise AssertionError(bad)
    for d in ("../x", "2026-13-01"):
        try:
            read_ledger(d, root=tmp_path)
        except ValueError:
            continue
        raise AssertionError(d)


def test_routes(tmp_path):
    append_ledger("note", "hello ledger", root=tmp_path)
    app = FastAPI()
    app.include_router(build_ledger_router(tmp_path))
    c = TestClient(app)
    assert c.get("/ledger/search", params={"q": "hello"}).json()["matches"][0]["line"] == "hello ledger"
    assert c.get(f"/ledger/{date.today().isoformat()}").json()["text"]
    assert c.get("/ledger/nope").status_code == 422
