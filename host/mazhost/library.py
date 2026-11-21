"""Library: reference material (Instagram saves, URLs, text) as Markdown files plus a
derived SQLite FTS5 index any agent can search fast.

Pure file/sqlite ops (no FastAPI), stdlib only. Layout under <dumps_dir>/library/:
  ig/ web/ text/   one <id>.md per item (frontmatter + Caption/Description/Notes)
  _drop/           drop an Instagram export .zip/.json or .txt/.url here, then scan_drop()
  library.db       derived index: safe to delete, rebuilt from the files
  INDEX.md         how-to for agents + counts (never a list of every item)
Writes reuse the dump inbox lock, atomic write and frontmatter helpers.

FTS5 is built into the stdlib sqlite3 on normal CPython builds. If a build lacks it, the
same table is a plain table and search() falls back to a LIKE scan (no bm25 ranking)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import dumps

DB = "library.db"
_DIRS = {"instagram": "ig", "web": "web", "text": "text"}
_SECTIONS = ("Caption", "Description", "Notes")
_POST = re.compile(r"https?://(?:www\.)?instagram\.com/(?:[\w.]+/)?(p|reels?|tv)/([A-Za-z0-9_-]+)", re.I)
_TRACKING = re.compile(r"^(utm_.*|igshid|igsh|fbclid|gclid)$", re.I)
_KNOWN_LISTS = ("saved_saved_media", "saved_saved_collections")
MAX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_MEMBERS = 5000
_COLS = "caption, description, notes, author, collection, tags, id, kind, url, project, saved, path"
_UNINDEXED = {"id", "kind", "url", "project", "saved", "path"}
_HEADER = """# Library

Reference material saved by Piblo (Instagram saves, URLs, text). Plain files, no server needed.

1. Search: `python host/nodinbox.py search "words" [--project X] [--limit N]`
   (or `GET /library/search?q=words` on Core). Results give the item file path: open it for the full item.
2. Each item is one Markdown file in `ig/`, `web/` or `text/`: JSON-valued frontmatter
   (id, kind, url, author, collection, saved, project, tags) then `## Caption`, `## Description`, `## Notes`.
   Put your own findings in `## Notes`; re-importing never erases it.
3. Add things: `python host/nodinbox.py import <instagram-export.zip-or-folder>`, or drop a file in `_drop/`.
4. `library.db` is a derived index: delete it any time, it rebuilds from the files.
"""


def _has_fts5() -> bool:
    try:
        sqlite3.connect(":memory:").execute("create virtual table t using fts5(a)")
        return True
    except sqlite3.Error:
        return False


_FTS5 = _has_fts5()


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _canon(url: str) -> str:
    p = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)])
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), query, ""))


def _split(body: str) -> dict:
    out = dict.fromkeys(_SECTIONS, "")
    parts = re.split(r"^## (Caption|Description|Notes)\n", body, flags=re.M)
    for name, content in zip(parts[1::2], parts[2::2]):
        out[name] = content.strip()
    return out


def _union(a: str, b: str) -> str:
    parts = [p for p in str(a or "").split("; ") if p]
    parts += [p for p in str(b or "").split("; ") if p and p not in parts]
    return "; ".join(parts)


# ---- index ----------------------------------------------------------------

def _newest(root: Path) -> int:
    return max(
        (e.stat().st_mtime_ns for sub in _DIRS.values() if (root / sub).is_dir()
         for e in os.scandir(root / sub) if e.name.endswith(".md")),
        default=0,
    )


def _stale(root: Path) -> bool:
    db = root / DB
    return not db.is_file() or _newest(root) > db.stat().st_mtime_ns


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("create table items(id text primary key)")
    cols = ", ".join(c + (" unindexed" if c in _UNINDEXED and _FTS5 else "") for c in _COLS.split(", "))
    conn.execute(f"create virtual table fts using fts5({cols})" if _FTS5 else f"create table fts({cols})")


def _upsert(conn: sqlite3.Connection, meta: dict, sec: dict, rel: str) -> None:
    row = conn.execute("select rowid from items where id=?", (meta["id"],)).fetchone()
    if row:
        rid = row[0]
        conn.execute("delete from fts where rowid=?", (rid,))
    else:
        rid = conn.execute("insert into items(id) values(?)", (meta["id"],)).lastrowid
    conn.execute(
        f"insert into fts(rowid, {_COLS}) values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, sec["Caption"], sec["Description"], sec["Notes"], meta.get("author", ""),
         meta.get("collection", ""), " ".join(map(str, meta.get("tags") or [])), meta["id"],
         meta.get("kind", ""), meta.get("url", ""), meta.get("project", ""), meta.get("saved", ""), rel),
    )


def _stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("select kind, collection, project from fts").fetchall()
    return {
        "total": len(rows),
        "kinds": dict(Counter(r[0] for r in rows)),
        "collections": dict(Counter(c for r in rows for c in r[1].split("; ") if c)),
        "projects": dict(Counter(r[2] for r in rows)),
    }


def _write_index(root: Path, conn: sqlite3.Connection) -> None:
    s = _stats(conn)
    lines = [_HEADER, f"## Counts\n\n{s['total']} items.\n"]
    for title, key in (("kind", "kinds"), ("project", "projects"), ("collection", "collections")):
        ranked = sorted(s[key].items(), key=lambda kv: (-kv[1], kv[0]))
        lines.append(f"### By {title}\n")
        lines += [f"- {name}: {n}" for name, n in ranked[:40]]
        if len(ranked) > 40:
            lines.append(f"- (+{len(ranked) - 40} more)")
        lines.append("")
    dumps._atomic_write(root / "INDEX.md", "\n".join(lines))


def _rebuild(root: Path, write_index: bool = True) -> int:
    """Rebuild library.db from the item files. Caller holds the lock."""
    tmp = root / (DB + ".tmp")
    tmp.unlink(missing_ok=True)
    n = 0
    with closing(sqlite3.connect(tmp)) as conn:
        _init(conn)
        for sub in _DIRS.values():
            for path in sorted((root / sub).glob("*.md")):
                try:
                    meta, body = dumps._parse(path.read_text(encoding="utf-8"))
                except OSError:
                    continue
                if meta.get("id") == path.stem:
                    _upsert(conn, meta, _split(body), f"{sub}/{path.name}")
                    n += 1
        if write_index:
            _write_index(root, conn)
        conn.commit()
    os.replace(tmp, root / DB)
    return n


def _prepare(root: Path) -> None:
    for sub in (*_DIRS.values(), "_drop"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def rebuild_index(root) -> int:
    root = Path(root)
    with dumps._lock(root):
        _prepare(root)
        return _rebuild(root)


def _fresh(root: Path) -> sqlite3.Connection:
    """Open the index, rebuilding first when it is missing or older than the newest item file."""
    if _stale(root):
        with dumps._lock(root):
            _prepare(root)
            if _stale(root):
                _rebuild(root)
    return sqlite3.connect(root / DB)


# ---- saving ---------------------------------------------------------------

def _save_one(root: Path, conn: sqlite3.Connection, it: dict, llm) -> dict:
    url = dumps._one_line(it.get("url"))
    text = str(it.get("text") or "").strip()
    m = _POST.match(url)
    if m:
        typ = "reel" if m[1].lower().startswith("reel") else m[1].lower()
        kind, item_id, url = "instagram", f"ig-{m[2]}", f"https://www.instagram.com/{typ}/{m[2]}/"
    elif url:
        url = _canon(url)
        kind, item_id = "web", f"web-{_hash(url)}"
    else:
        kind, item_id = "text", f"text-{_hash(' '.join(text.split()).lower())}"
    rel = f"{_DIRS[kind]}/{item_id}.md"
    path = root / rel
    old_text = path.read_text(encoding="utf-8") if path.is_file() else None
    old, body = dumps._parse(old_text) if old_text is not None else ({}, "")
    sec = _split(body)
    sec["Caption"] = sec["Caption"] or text
    if it.get("notes") and it["notes"] not in sec["Notes"]:
        sec["Notes"] = "\n\n".join(filter(None, (sec["Notes"], str(it["notes"]).strip())))
    collection = _union(old.get("collection"), dumps._one_line(it.get("collection")))
    project = dumps._one_line(it.get("project"))
    if not project:
        project = old.get("project")
        if not project or project == "unassigned":
            project = dumps.assign_project(root.parent, f"{sec['Caption']}\n{it.get('author') or ''}", collection, llm)
    meta = {
        "id": item_id,
        "kind": kind,
        "url": url,
        "author": old.get("author") or dumps._one_line(it.get("author")).lstrip("@"),
        "collection": collection,
        "media": old.get("media") or dumps._one_line(it.get("media")),
        "saved": old.get("saved") or dumps._one_line(it.get("saved")),
        "ingested": old.get("ingested") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": project,
        "tags": list(dict.fromkeys([*(old.get("tags") or []), *(it.get("tags") or [])])),
        "enriched": bool(old.get("enriched")),
        "source": old.get("source") or dumps._one_line(it.get("source") or "api"),
    }
    new_text = dumps._render(meta, "\n\n".join(f"## {name}\n{sec[name]}" for name in _SECTIONS) + "\n")
    if new_text != old_text:  # a re-import of unchanged items writes no files
        dumps._atomic_write(path, new_text)
    _upsert(conn, meta, sec, rel)
    return {"id": item_id, "status": "updated" if old_text is not None else "added", "project": project}


def save_items(root, items, llm: Callable[[str, str], str] | None = None) -> list[dict]:
    """Save many items with one index transaction and one INDEX.md refresh. Each item is a dict with
    url, text, author, collection, media, saved, project, source (all optional)."""
    root = Path(root)
    out = []
    with dumps._lock(root):
        _prepare(root)
        if _stale(root):
            _rebuild(root, write_index=False)
        with closing(sqlite3.connect(root / DB)) as conn:
            for it in items:
                out.append(_save_one(root, conn, it, llm))
            conn.commit()
            _write_index(root, conn)
    return out


def save_item(root, *, kind="url", url="", text="", author="", collection="", media="", saved="",
              project=None, source="api", llm=None, notes="", tags=None) -> dict:
    """Save one item. `kind` is only a hint: an Instagram post URL is always kind instagram, any
    other URL web, no URL text. Re-saving the same canonical URL updates and never erases
    Caption/Description/Notes. Returns {id, status: added|updated, project}."""
    item = dict(kind=kind, url=url, text=text, author=author, collection=collection, media=media,
                saved=saved, project=project, source=source, notes=notes, tags=tags)
    return save_items(root, [item], llm)[0]


# ---- Instagram export import ---------------------------------------------

def _fix(value) -> str:
    """Instagram exports often store UTF-8 bytes as latin-1 escapes; repair when it round-trips."""
    s = str(value or "")
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _when(ts) -> str:
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        try:
            return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, timezone.utc).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return ""
    return dumps._one_line(ts) if isinstance(ts, str) else ""


def _wanted(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return (name.lower().endswith(".json") and "saved" in name.lower()
            and ".." not in parts and not name.startswith(("/", "\\")) and not re.match(r"^[A-Za-z]:", name))


def _load(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return None


def _json_files(path: Path):
    """Yield (name, parsed json) for saved-related .json files in a zip or folder. Zip members are
    read in memory, never extracted, so member names cannot write anywhere."""
    if path.is_dir():
        for dirpath, _, names in os.walk(path):
            for fname in names:
                f = Path(dirpath) / fname
                if _wanted(str(f.relative_to(path))) and f.stat().st_size <= MAX_MEMBER_BYTES:
                    data = _load(f.read_bytes())
                    if data is not None:
                        yield str(f), data
    elif path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist()[:MAX_MEMBERS]:
                if info.is_dir() or not _wanted(info.filename) or info.file_size > MAX_MEMBER_BYTES:
                    continue
                with zf.open(info) as fh:
                    raw = fh.read(MAX_MEMBER_BYTES + 1)
                data = _load(raw) if len(raw) <= MAX_MEMBER_BYTES else None
                if data is not None:
                    yield info.filename, data
    elif path.is_file() and path.suffix.lower() == ".json" and path.stat().st_size <= MAX_MEMBER_BYTES:
        data = _load(path.read_bytes())
        if data is not None:
            yield path.name, data


def _walk(node, st: dict, title: str = "") -> None:
    """Collect posts into st['posts'] and count unusable entries in st['skipped'].
    Handles string_map_data as a dict or a list; a collection Name entry (value, no href) sets the
    collection for the post entries after it; any other dict with a post href is taken with the
    nearest title/timestamp."""
    if isinstance(node, list):
        for x in node:
            _walk(x, st, title)
    elif isinstance(node, dict):
        smd = node.get("string_map_data")
        smd_items = list(smd.values()) if isinstance(smd, dict) else smd if isinstance(smd, list) else []
        cells = [c for c in smd_items if isinstance(c, dict)]
        link = next(((m, c) for c in [node, *cells] if isinstance(c.get("href"), str)
                     for m in [_POST.match(c["href"].strip())] if m), None)
        if link:
            m, cell = link
            post = st["posts"].setdefault(m[2], {"url": m[0], "author": "", "saved": "", "colls": []})
            raw = node.get("title") or title or cell.get("value")
            post["author"] = post["author"] or (_fix(raw).strip().lstrip("@") if isinstance(raw, str) else "")
            post["saved"] = post["saved"] or _when(cell.get("timestamp") or node.get("timestamp"))
            if st["coll"] and st["coll"] not in post["colls"]:
                post["colls"].append(st["coll"])
            return
        if smd is not None:
            name = smd.get("Name") if isinstance(smd, dict) else next((c for c in cells if c.get("label") == "Name"), None)
            value = name.get("value") if isinstance(name, dict) and not name.get("href") else None
            if isinstance(value, str) and value.strip():
                st["coll"] = dumps._one_line(_fix(value))
                st["collections"].add(st["coll"])
            else:
                st["skipped"] += 1
            return
        for key, value in node.items():
            if key in _KNOWN_LISTS and isinstance(value, list):
                st["skipped"] += sum(not isinstance(x, (dict, list)) for x in value)
            if isinstance(value, (dict, list)):
                _walk(value, st, node["title"] if isinstance(node.get("title"), str) else title)


def import_instagram_export(root, path) -> dict:
    """Import saved posts and collections from an export .zip, an extracted folder, or one JSON file.
    A bad entry never crashes the import: it counts as skipped. Keyword project matching only (no
    LLM per item). Returns {found, added, updated, skipped, collections}; found = distinct posts."""
    st = {"posts": {}, "skipped": 0, "collections": set(), "coll": ""}
    for _, data in _json_files(Path(path)):
        st["coll"] = ""
        _walk(data, st)
    items = [
        dict(kind="instagram", url=p["url"], author=p["author"], saved=p["saved"],
             collection="; ".join(p["colls"]), source="ig-export")
        for p in st["posts"].values()
    ]
    results = save_items(root, items) if items else []
    return {
        "found": len(items),
        "added": sum(r["status"] == "added" for r in results),
        "updated": sum(r["status"] == "updated" for r in results),
        "skipped": st["skipped"],
        "collections": len(st["collections"]),
    }


def scan_drop(root) -> dict:
    """Import everything in _drop/: .zip/.json that hold Instagram saves (then moved to _drop/done/),
    .txt/.url files as url or text items. Anything else stays put and counts as ignored."""
    root = Path(root)
    _prepare(root)
    drop = root / "_drop"
    total = {"files": 0, "found": 0, "added": 0, "updated": 0, "skipped": 0, "ignored": 0}
    for f in sorted(p for p in drop.iterdir() if p.is_file()):
        suffix = f.suffix.lower()
        if suffix in (".zip", ".json"):
            counts = import_instagram_export(root, f)
            if not counts["found"]:
                total["ignored"] += 1
                continue
        elif suffix in (".txt", ".url") and (raw := f.read_text(encoding="utf-8", errors="replace").strip()):
            link = re.search(r"^URL=(\S+)", raw, re.M) if suffix == ".url" else None
            one = link[1] if link else raw if re.fullmatch(r"https?://\S+", raw) else ""
            res = save_item(root, url=one, text="" if one else raw, source="drop")
            counts = {"found": 1, "added": res["status"] == "added", "updated": res["status"] == "updated", "skipped": 0}
        else:
            total["ignored"] += 1
            continue
        for key in ("found", "added", "updated", "skipped"):
            total[key] += int(counts[key])
        total["files"] += 1
        (drop / "done").mkdir(exist_ok=True)
        dest = drop / "done" / f.name
        if dest.exists():
            dest = drop / "done" / f"{int(time.time())}-{f.name}"
        shutil.move(str(f), dest)
    return total


# ---- search ---------------------------------------------------------------

def search(root, q: str, *, project: str | None = None, kind: str | None = None, limit: int = 10) -> list[dict]:
    """bm25-ranked search. Every term of q is quoted for FTS5, so user text is never FTS syntax."""
    root = Path(root)
    terms = re.findall(r"\w+", q or "")
    if not terms:
        return []
    kind = {"url": "web"}.get(kind, kind)
    limit = max(1, min(int(limit), 100))
    where, args = "", []
    for col, val in (("project", project), ("kind", kind)):
        if val:
            where += f" and {col} = ?"
            args.append(val)
    with closing(_fresh(root)) as conn:
        if _FTS5:
            match = " ".join('"' + t.replace('"', '""') + '"' for t in terms)
            sql = ("select id, kind, url, author, collection, project, saved, "
                   "snippet(fts, -1, '[', ']', '...', 12), path from fts "
                   f"where fts match ?{where} order by bm25(fts) limit ?")
            rows = conn.execute(sql, [match, *args, limit]).fetchall()
        else:
            hay = "(caption||' '||description||' '||notes||' '||author||' '||collection||' '||tags)"
            like = "".join(f" and {hay} like ? escape '\\'" for _ in terms)
            esc = ["%" + re.sub(r"([\\%_])", r"\\\1", t) + "%" for t in terms]
            sql = ("select id, kind, url, author, collection, project, saved, substr(caption, 1, 160), path "
                   f"from fts where 1=1{like}{where} order by saved desc limit ?")
            rows = conn.execute(sql, [*esc, *args, limit]).fetchall()
    keys = ("id", "kind", "url", "author", "collection", "project", "saved", "snippet", "path")
    return [{**dict(zip(keys, r)), "path": str(root / r[8])} for r in rows]


def stats(root) -> dict:
    """Counts per kind, collection and project."""
    with closing(_fresh(Path(root))) as conn:
        return _stats(conn)


def recent(root, limit: int = 10, kind: str | None = None) -> list[dict]:
    """Recent saves from the same Markdown source used by the search index."""
    root = Path(root)
    rows = []
    folders = [_DIRS[kind]] if kind in _DIRS else list(_DIRS.values())
    for sub in folders:
        for path in (root / sub).glob("*.md"):
            try:
                meta, body = dumps._parse(path.read_text(encoding="utf-8"))
                if meta.get("id") != path.stem:
                    continue
                sections = _split(body)
                rows.append({**meta, "title": sections["Caption"][:280] or meta.get("url") or path.stem,
                             "text": sections["Caption"], "notes": sections["Notes"],
                             "url": meta.get("url", ""), "path": str(path),
                             "when": meta.get("ingested") or meta.get("saved") or
                             datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
            except (OSError, UnicodeError):
                continue
    return sorted(rows, key=lambda row: row["when"], reverse=True)[:limit]
