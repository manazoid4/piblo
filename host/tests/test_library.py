"""Library tests. All handles, URLs and text are synthetic."""

from __future__ import annotations

import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from mazhost import dumps, library
from mazhost.app import create_app
from mazhost.config import Settings
import nodinbox

SAVED = "instagram-export/your_instagram_activity/saved/"


def mojibake(s: str) -> str:
    return s.encode("utf-8").decode("latin-1")


def posts_json(entries):
    return json.dumps({"saved_saved_media": entries})


def post(handle, href, ts=1700000000):
    return {"title": handle, "string_map_data": {"Saved on": {"href": href, "timestamp": ts}}}


def export_zip(path):
    posts = [
        post("alpha_user", "https://www.instagram.com/p/AAA111/"),
        post(mojibake("café_chef"), "https://www.instagram.com/reel/BBB222/"),
        post("dup_user", "https://www.instagram.com/p/AAA111?igshid=xyz"),  # same post, other form
        {"title": "broken", "string_map_data": {"Saved on": {"timestamp": 5}}},  # malformed: no href
        "garbage",  # malformed: not an entry
    ]
    collections = {"saved_saved_collections": [
        {"title": "Collections", "string_map_data": {"Name": {"value": "Recipe ideas", "timestamp": 1}}},
        {"title": "alpha_user", "string_map_data": {"Name": {"href": "https://www.instagram.com/p/AAA111/"}}},
        {"title": "x", "string_map_data": {"Name": {"href": "https://www.instagram.com/reel/BBB222/"}}},
        {"title": "Collections", "string_map_data": {"Name": {"value": mojibake("Café tools"), "timestamp": 2}}},
        {"title": "zed_user", "string_map_data": {"Name": {"href": "https://www.instagram.com/p/CCC333/"}}},
    ]}
    odd = {"weird": {"nested": [{"title": "gamma_user", "href": "https://www.instagram.com/reels/DDD444/",
                                 "timestamp": 1700000500}]}}
    evil = posts_json([post("evil", "https://www.instagram.com/p/EEE555/")])
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(SAVED + "saved_posts.json", posts_json(posts))
        zf.writestr(SAVED + "saved_collections.json", json.dumps(collections))
        zf.writestr(SAVED + "saved_extra.json", json.dumps(odd))
        zf.writestr("../../evil/saved_posts.json", evil)
        zf.writestr("/abs/saved_posts.json", evil)
        zf.writestr("C:/win/saved_posts.json", evil)
        zf.writestr("messages/inbox.json", evil)  # not a saved file
        zf.writestr(SAVED + "readme.txt", "x")
    return path


def read_item(root, item_id):
    sub = {"ig": "ig", "we": "web", "te": "text"}[item_id[:2]]
    meta, body = dumps._parse((root / sub / f"{item_id}.md").read_text(encoding="utf-8"))
    return meta, body


@pytest.fixture()
def root(tmp_path):
    return tmp_path / "inbox" / "library"


def test_import_counts_ids_collections_and_traversal(tmp_path, root):
    r = library.import_instagram_export(root, export_zip(tmp_path / "e.zip"))
    assert r == {"found": 4, "added": 4, "updated": 0, "skipped": 2, "collections": 2}
    assert sorted(p.stem for p in (root / "ig").glob("*.md")) == ["ig-AAA111", "ig-BBB222", "ig-CCC333", "ig-DDD444"]
    meta, body = read_item(root, "ig-AAA111")
    assert meta["kind"] == "instagram" and meta["url"] == "https://www.instagram.com/p/AAA111/"
    assert meta["author"] == "alpha_user" and meta["collection"] == "Recipe ideas"
    assert meta["saved"].startswith("2023-11-14") and meta["enriched"] is False and meta["tags"] == []
    assert list(meta)[:11] == ["id", "kind", "url", "author", "collection", "media", "saved", "ingested",
                               "project", "tags", "enriched"]
    assert "## Caption\n" in body and "## Description\n" in body and "## Notes\n" in body
    assert read_item(root, "ig-BBB222")[0]["author"] == "café_chef"  # mojibake repaired
    assert read_item(root, "ig-CCC333")[0]["collection"] == "Café tools"
    assert read_item(root, "ig-DDD444")[0]["author"] == "gamma_user"  # unknown shape, nearest title
    assert read_item(root, "ig-DDD444")[0]["url"].endswith("/reel/DDD444/")
    assert not (root / "ig" / "ig-EEE555.md").exists()  # traversal / non-saved members ignored
    assert not (tmp_path.parent / "evil").exists() and not (tmp_path / "evil").exists()
    assert len(list(library._json_files(tmp_path / "e.zip"))) == 3
    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "4 items" in index and "Recipe ideas: 2" in index and "ig-AAA111" not in index


def test_folder_import(tmp_path, root):
    folder = tmp_path / "x"
    with zipfile.ZipFile(export_zip(tmp_path / "e.zip")) as zf:
        zf.extractall(folder, [n for n in zf.namelist() if n.startswith("instagram-export/")])
    assert library.import_instagram_export(root, folder)["found"] == 4


def test_reimport_keeps_notes_and_dedupes(tmp_path, root):
    export_zip(tmp_path / "e.zip")
    library.import_instagram_export(root, tmp_path / "e.zip")
    path = root / "ig" / "ig-AAA111.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("## Caption\n", "## Caption\nmy caption words\n", 1)
                    .replace("## Notes\n", "## Notes\nagent found a thing\n", 1), encoding="utf-8")
    r = library.import_instagram_export(root, tmp_path / "e.zip")
    assert (r["added"], r["updated"]) == (0, 4)
    meta, body = read_item(root, "ig-AAA111")
    assert "agent found a thing" in body and "my caption words" in body
    # other URL forms of the same post are the same item and never erase text
    for form in ("https://instagram.com/p/AAA111", "http://www.instagram.com/someone/p/AAA111/?x=1#f",
                 "https://www.instagram.com/p/AAA111/"):
        res = library.save_item(root, kind="instagram", url=form, text="other caption")
        assert res["id"] == "ig-AAA111" and res["status"] == "updated"
    assert "my caption words" in read_item(root, "ig-AAA111")[1] and "other caption" not in read_item(root, "ig-AAA111")[1]
    assert len(list((root / "ig").glob("*.md"))) == 4
    assert library.save_item(root, url="https://www.instagram.com/reels/QQQ9/")["id"] == "ig-QQQ9"


def test_web_and_text_ids(root):
    a = library.save_item(root, kind="url", url="https://Example.com/page/?utm_source=x&id=2#top", text="body one")
    b = library.save_item(root, kind="url", url="https://example.com/page?id=2")
    assert a["id"] == b["id"] and a["id"].startswith("web-") and (a["status"], b["status"]) == ("added", "updated")
    t1 = library.save_item(root, kind="text", text="  Remember   the milk ")
    t2 = library.save_item(root, kind="text", text="remember the milk")
    assert t1["id"] == t2["id"] and t1["id"].startswith("text-") and (root / "text" / f"{t1['id']}.md").is_file()


def test_search_ranking_filters_and_snippet(root):
    library.save_item(root, text="espresso espresso espresso machine", project="coffee", author="a")
    library.save_item(root, text="a very long note about gardening and soil and many other things "
                                 "that finally mentions espresso once near the end of it", project="garden")
    library.save_item(root, kind="url", url="https://example.com/tea", text="green tea", project="coffee")
    hits = library.search(root, "espresso")
    assert [h["project"] for h in hits] == ["coffee", "garden"]
    assert set(hits[0]) == {"id", "kind", "url", "author", "collection", "project", "saved", "snippet", "path"}
    assert "[espresso]" in hits[0]["snippet"] and hits[0]["path"].endswith(f"{hits[0]['id']}.md")
    assert [h["project"] for h in library.search(root, "espresso", project="garden")] == ["garden"]
    assert [h["kind"] for h in library.search(root, "tea", kind="url")] == ["web"]
    assert library.search(root, "tea", kind="text") == []
    assert len(library.search(root, "espresso", limit=1)) == 1
    assert library.search(root, "   ") == []


@pytest.mark.parametrize("q", ['"', '*', 'OR', 'NEAR(', 'a" OR "b', '"unterminated', 'caption:x', 'NOT', '-', '^foo',
                               'x AND (y', "' OR 1=1 --", 'NEAR(a b, 2)', '{caption}: x'])
def test_hostile_queries_never_raise(root, q):
    library.save_item(root, text="this or that, near the star * and a quote \" here")
    assert isinstance(library.search(root, q), list)


def test_operator_words_are_literal(root):
    library.save_item(root, text="this or that")
    assert len(library.search(root, "OR")) == 1  # as FTS syntax this would be an error
    assert library.search(root, "this OR missingword") == []  # all terms must match, OR is not an operator


def test_index_rebuilds_when_missing_or_stale(root):
    item = library.save_item(root, text="first words")
    (root / library.DB).unlink()
    assert [h["id"] for h in library.search(root, "first")] == [item["id"]]
    time.sleep(0.05)  # coarse file clocks
    path = root / "text" / f"{item['id']}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("## Notes\n", "## Notes\nzebrafish\n"), encoding="utf-8")
    assert [h["id"] for h in library.search(root, "zebrafish")] == [item["id"]]
    assert library.rebuild_index(root) == 1


def test_like_fallback_without_fts5(root, monkeypatch):
    monkeypatch.setattr(library, "_FTS5", False)
    library.save_item(root, text="50%_off sale on bikes", project="p")
    assert len(library.search(root, "bikes")) == 1
    assert library.search(root, "bikes", project="other") == []
    assert len(library.search(root, "50")) == 1


def test_scan_drop(tmp_path, root):
    library.save_item(root, text="seed")  # creates _drop
    drop = root / "_drop"
    export_zip(drop / "export.zip")
    (drop / "idea.txt").write_text("a loose idea about kites", encoding="utf-8")
    (drop / "link.url").write_text("[InternetShortcut]\nURL=https://example.com/kites\n", encoding="utf-8")
    (drop / "junk.bin").write_bytes(b"\x00")
    (drop / "other.json").write_text('{"nothing": 1}', encoding="utf-8")
    r = library.scan_drop(root)
    assert r == {"files": 3, "found": 6, "added": 6, "updated": 0, "skipped": 2, "ignored": 2}
    assert (drop / "done" / "export.zip").is_file() and not (drop / "export.zip").exists()
    assert (drop / "junk.bin").exists() and (drop / "other.json").exists()
    assert library.search(root, "kites") and library.stats(root)["total"] == 7


def test_bulk_3000_items_fast_and_one_index_refresh(tmp_path, root, monkeypatch):
    entries = [post(f"user{i % 50}", f"https://www.instagram.com/p/BULK{i:05d}/", 1700000000 + i) for i in range(3000)]
    colls = {"saved_saved_collections": [
        {"title": "Collections", "string_map_data": {"Name": {"value": "Bulk set", "timestamp": 1}}},
        *[{"title": "u", "string_map_data": {"Name": {"href": f"https://www.instagram.com/p/BULK{i:05d}/"}}}
          for i in range(0, 3000, 3)]]}
    zpath = tmp_path / "bulk.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(SAVED + "saved_posts.json", posts_json(entries))
        zf.writestr(SAVED + "saved_collections.json", json.dumps(colls))
    calls = []
    real = library._write_index
    monkeypatch.setattr(library, "_write_index", lambda *a: (calls.append(1), real(*a))[1])
    start = time.perf_counter()
    r = library.import_instagram_export(root, zpath)
    import_s = time.perf_counter() - start
    assert r == {"found": 3000, "added": 3000, "updated": 0, "skipped": 0, "collections": 1}
    assert len(calls) == 1
    start = time.perf_counter()
    again = library.import_instagram_export(root, zpath)
    reimport_s = time.perf_counter() - start
    assert again["updated"] == 3000 and again["added"] == 0
    start = time.perf_counter()  # second pass: file reads are warm (the first read of a fresh file can be slow under antivirus)
    library.import_instagram_export(root, zpath)
    warm_reimport_s = time.perf_counter() - start
    start = time.perf_counter()
    hits = library.search(root, "user7 Bulk", limit=5)
    search_s = time.perf_counter() - start
    start = time.perf_counter()
    for _ in range(20):
        library.search(root, "user7")
    warm_ms = (time.perf_counter() - start) / 20 * 1000
    print(f"\nTIMING import(3000)={import_s:.2f}s first_reimport(3000)={reimport_s:.2f}s warm_reimport(3000)={warm_reimport_s:.2f}s "
          f"first_search={search_s * 1000:.0f}ms warm_search={warm_ms:.1f}ms")
    assert import_s < 30 and warm_reimport_s < 30 and len(hits) == 5 and warm_ms < 500
    assert library.stats(root)["total"] == 3000
    assert len((root / "INDEX.md").read_text(encoding="utf-8").splitlines()) < 60


# ---- routes + CLI ---------------------------------------------------------

class Stt:
    def available(self):
        return True

    def transcribe(self, _path):
        return "words"


class Models:
    def status(self):
        return {}

    def chat(self, messages, route):
        return "unassigned", "local"


TOKEN = "test-token-that-is-not-default"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make(tmp_path, **kw):
    cfg = Settings(token=TOKEN, _env_file=None, dumps_dir=str(tmp_path / "inbox"), **kw)
    return TestClient(create_app(cfg, stt=Stt(), models=Models()))


def test_ingest_and_search_routes(tmp_path):
    client = make(tmp_path)
    body = {"kind": "url", "url": "https://example.com/post", "text": "falcon notes", "collection": "birds"}
    assert client.post("/ingest", json=body).status_code == 401
    assert client.get("/library/search", params={"q": "falcon"}).status_code == 401
    assert client.get("/library/stats").status_code == 401
    assert client.post("/library/scan").status_code == 401
    assert client.post("/ingest/file", files={"file": ("a.zip", b"x")}).status_code == 401
    first = client.post("/ingest", headers=AUTH, json=body)
    assert first.status_code == 200, first.text
    assert set(first.json()) == {"id", "status", "project"} and first.json()["status"] == "added"
    assert client.post("/ingest", headers=AUTH, json=body).json()["status"] == "updated"
    ig = client.post("/ingest", headers=AUTH, json={"kind": "instagram", "url": "https://www.instagram.com/p/ZZZ1/?igshid=1",
                                                     "author": "synthetic_user", "project": "birdwatch"})
    assert ig.json() == {"id": "ig-ZZZ1", "status": "added", "project": "birdwatch"}
    note = client.post("/ingest", headers=AUTH, json={"kind": "text", "text": "falcon in the garden"}).json()
    assert note["id"].startswith("text-")
    found = client.get("/library/search", headers=AUTH, params={"q": "falcon"}).json()["results"]
    assert {r["id"] for r in found} == {first.json()["id"], note["id"]}
    only = client.get("/library/search", headers=AUTH, params={"q": "falcon", "kind": "text"}).json()["results"]
    assert [r["id"] for r in only] == [note["id"]]
    assert client.get("/library/search", headers=AUTH, params={"q": 'x" OR *'}).json() == {"results": []}
    assert client.get("/library/search", headers=AUTH, params={"q": "falcon", "limit": 0}).status_code == 422
    stats = client.get("/library/stats", headers=AUTH).json()
    assert stats["total"] == 3 and stats["kinds"] == {"web": 1, "instagram": 1, "text": 1}
    assert stats["collections"] == {"birds": 1} and stats["projects"]["birdwatch"] == 1


@pytest.mark.parametrize("body", [
    {"kind": "url", "url": "ftp://example.com/x"},
    {"kind": "url", "url": "javascript:alert(1)"},
    {"kind": "url"},
    {"kind": "url", "url": "https://example.com/" + "a" * 2000},
    {"kind": "text", "text": "x" * 50_001},
    {"kind": "text", "text": "   "},
    {"kind": "instagram", "url": "https://example.com/p/abc/"},
    {"kind": "nope", "text": "x"},
])
def test_ingest_validation(tmp_path, body):
    assert make(tmp_path).post("/ingest", headers=AUTH, json=body).status_code == 422


def test_ingest_file_route(tmp_path):
    client = make(tmp_path, max_upload_mb=1)
    export_zip(tmp_path / "e.zip")
    ok = client.post("/ingest/file", headers=AUTH, files={"file": ("e.zip", (tmp_path / "e.zip").read_bytes())})
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"found": 4, "added": 4, "updated": 0, "skipped": 2, "collections": 2}
    assert client.post("/ingest/file", headers=AUTH, files={"file": ("e.txt", b"x")}).status_code == 415
    big = client.post("/ingest/file", headers=AUTH, files={"file": ("e.zip", b"0" * (1024 * 1024 + 10))})
    assert big.status_code == 413
    bad = client.post("/ingest/file", headers=AUTH, files={"file": ("e.json", b"not json")})
    assert bad.status_code == 200 and bad.json()["found"] == 0
    drop = tmp_path / "inbox" / "library" / "_drop"
    (drop / "n.txt").write_text("scan me please", encoding="utf-8")
    assert client.post("/library/scan", headers=AUTH).json()["added"] == 1


def test_cli_search_import_stats(tmp_path, capsys):
    base = ["--dir", str(tmp_path / "inbox")]
    export_zip(tmp_path / "e.zip")
    assert nodinbox.main(base + ["import", str(tmp_path / "e.zip")]) == 0
    assert "found 4, added 4, updated 0, skipped 2, collections 2" in capsys.readouterr().out
    assert nodinbox.main(base + ["search", "gamma_user"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2 and lines[0].startswith("ig-DDD444") and "gamma_user" in lines[0]
    assert lines[1].strip().startswith("https://www.instagram.com/reel/DDD444/")
    assert nodinbox.main(base + ["search", "gamma_user", "--project", "nope", "--limit", "3"]) == 0
    assert capsys.readouterr().out == ""
    assert nodinbox.main(base + ["stats"]) == 0
    assert "4 items" in capsys.readouterr().out


def test_get_delete_export_roundtrip(tmp_path, root):
    web = library.save_item(root, url="https://example.com/recipe?utm_source=x", text="sourdough starter",
                            notes="feed 1:1:1", source="drop", author="synthetic")
    ig = library.save_item(root, url="https://www.instagram.com/p/AAA111/", author="alpha_user", source="ig-export")
    txt = library.save_item(root, text="fix the fence gate", notes="measure twice", source="drop")
    got = library.get_item(root, web["id"])
    assert got["url"] == "https://example.com/recipe" and got["Notes"] == "feed 1:1:1" and got["source"] == "drop"
    assert library.get_item(root, "web-nope") is None
    assert library.delete_item(root, ig["id"]) is True
    assert not (root / "ig" / f"{ig['id']}.md").exists()
    assert library.stats(root)["total"] == 2 and library.search(root, "alpha_user") == []
    assert library.delete_item(root, ig["id"]) is False          # revoking twice is safe
    items = library.export_items(root)
    assert {i["id"] for i in items} == {web["id"], txt["id"]}
    root2 = tmp_path / "other" / "library"                        # export re-imports elsewhere, notes intact
    again = library.save_items(root2, [
        dict(url=i.get("url", ""), text=i.get("Caption", ""), author=i.get("author", ""),
             collection=i.get("collection", ""), notes=i.get("Notes", ""), tags=i.get("tags"),
             project=i.get("project"), source=i.get("source", ""), saved=i.get("saved", ""))
        for i in items])
    assert {r["id"] for r in again} == {web["id"], txt["id"]}
    assert library.get_item(root2, txt["id"])["Notes"] == "measure twice"


@pytest.mark.parametrize("bad", ["../../evil", "web-../../evil", "ig-A/../../x", "nope-1", "", "web-"])
def test_delete_and_get_reject_hostile_ids(root, bad):
    library.save_item(root, url="https://example.com/keep", text="keep me")
    assert library.get_item(root, bad) is None
    assert library.delete_item(root, bad) is False
    assert library.search(root, "keep")                           # nothing touched


def test_item_delete_export_routes(tmp_path):
    client = make(tmp_path)
    assert client.get("/library/item/web-x").status_code == 401
    assert client.delete("/library/item/web-x").status_code == 401
    assert client.get("/library/export").status_code == 401
    saved = client.post("/ingest", headers=AUTH, json={"kind": "url", "url": "https://example.com/one", "text": "otter"})
    iid = saved.json()["id"]
    got = client.get(f"/library/item/{iid}", headers=AUTH)
    assert got.status_code == 200 and got.json()["url"] == "https://example.com/one"
    assert client.get("/library/item/web-missing", headers=AUTH).status_code == 404
    assert [i["id"] for i in client.get("/library/export", headers=AUTH).json()["items"]] == [iid]
    assert client.delete(f"/library/item/{iid}", headers=AUTH).json() == {"ok": True, "id": iid}
    assert client.delete(f"/library/item/{iid}", headers=AUTH).status_code == 404
    assert client.get("/library/search", headers=AUTH, params={"q": "otter"}).json() == {"results": []}
    assert client.get("/library/export", headers=AUTH).json() == {"items": []}
