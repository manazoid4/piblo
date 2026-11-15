"""Dump inbox: brain dumps saved as Markdown files any agent on the PC can read.

Pure file ops (no FastAPI). One file per dump, plus a regenerated INDEX.md.
All writes go through one inbox-wide lock file so two agents cannot race."""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

STATUSES = ("new", "claimed", "done")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
_STARTER_PROJECTS = [
    {"name": "example-project-a", "keywords": ["project a", "example-a"], "path": ""},
    {"name": "example-project-b", "keywords": ["project b", "example-b"], "path": ""},
    {"name": "personal", "keywords": ["groceries", "holiday"], "path": ""},
]
_HEADER = """# Dump inbox

Voice brain dumps saved by Piblo. Any agent can use this folder, no server needed.

1. Read the open list below (newest first). Open the linked file for the full transcript.
2. Claim before working so nobody else picks it up:
   `python host/nodinbox.py claim <id> --agent <your-name>` (exit code 1 = already claimed)
3. When finished: `python host/nodinbox.py done <id> --agent <your-name> --note "what you did"`
4. Other commands: `list [--all] [--project X]`, `show <id>`, `add "text"`.
   Set MAZ_DUMPS_DIR to point the CLI at a different inbox.
"""


class DumpError(Exception):
    """Bad id, missing dump, or a claim conflict. Message is safe to show."""


def _one_line(value) -> str:
    return " ".join(str(value if value is not None else "").split())


def _check_id(dump_id: str) -> str:
    if not isinstance(dump_id, str) or not _ID_RE.match(dump_id):
        raise DumpError("invalid_dump_id")
    return dump_id


def _path(inbox: Path, dump_id: str) -> Path:
    return Path(inbox) / f"{_check_id(dump_id)}.md"


@contextmanager
def _lock(inbox: Path):
    inbox.mkdir(parents=True, exist_ok=True)
    lock = inbox / ".inbox.lock"
    deadline = time.monotonic() + 5
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:  # a crashed writer must not wedge the inbox forever
                if time.time() - lock.stat().st_mtime > 30:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise DumpError("inbox_busy")
            time.sleep(0.02)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _render(meta: dict, body: str) -> str:
    lines = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in meta.items()]
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def _parse(text: str) -> tuple[dict, str]:
    meta: dict = {}
    if not text.startswith("---\n"):
        return meta, text
    head, _, body = text[4:].partition("\n---\n")
    for line in head.splitlines():
        key, _, raw = line.partition(": ")
        try:
            meta[key] = json.loads(raw)
        except json.JSONDecodeError:
            meta[key] = raw
    if "created" in meta:
        try:
            if datetime.fromisoformat(meta["created"]).tzinfo is None:
                raise ValueError("missing timezone")
        except (TypeError, ValueError):
            meta["created"] = ""
    return meta, body


def _read(inbox: Path, dump_id: str) -> tuple[Path, dict, str]:
    path = _path(inbox, dump_id)
    if not path.is_file():
        raise DumpError("dump_not_found")
    meta, body = _parse(path.read_text(encoding="utf-8"))
    return path, meta, body


def _bullets(items, checkbox=False) -> str:
    prefix = "- [ ] " if checkbox else "- "
    return "\n".join(prefix + _one_line(i) for i in items or []) or "(none)"


def save_dump(
    inbox: Path,
    transcript: str,
    structured: dict | None,
    *,
    source: str,
    kind: str = "braindump",
    project: str | None = None,
) -> dict:
    """Write one dump file and refresh INDEX.md. Returns the frontmatter dict.
    `structured` may be None/partial: the transcript is always kept."""
    inbox = Path(inbox)
    data = structured or {}
    now = datetime.now(timezone.utc)
    summary = _one_line(data.get("summary")) or _one_line(transcript)[:200] or "(empty)"
    slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")[:30].strip("-") or "dump"
    stem = f"{now:%Y-%m-%dT%H%M%S}-{slug}"
    body = (
        f"## Summary\n{summary}\n\n## Actions\n{_bullets(data.get('actions'), True)}\n\n"
        f"## Ideas\n{_bullets(data.get('ideas'))}\n\n## Questions\n{_bullets(data.get('questions'))}\n\n"
        f"## Transcript\n{transcript or ''}\n"
    )
    with _lock(inbox):
        dump_id, n = stem, 2
        while (inbox / f"{dump_id}.md").exists():
            dump_id, n = f"{stem}-{n}", n + 1
        meta = {
            "id": dump_id,
            "created": now.isoformat(timespec="seconds"),
            "source": _one_line(source),
            "kind": _one_line(kind),
            "project": _one_line(project) or "unassigned",
            "status": "new",
            "claimed_by": "",
            "summary": summary,
        }
        _atomic_write(inbox / f"{dump_id}.md", _render(meta, body))
        _write_index(inbox)
    return meta


def _all(inbox: Path) -> list[dict]:
    out = []
    for path in Path(inbox).glob("*.md"):
        if path.name == "INDEX.md":
            continue
        try:
            meta, _ = _parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if meta.get("id") == path.stem:
            out.append(meta)
    return sorted(out, key=lambda m: m.get("created", ""), reverse=True)


def list_dumps(inbox: Path, status: str | None = None, project: str | None = None) -> list[dict]:
    return [
        m for m in _all(inbox)
        if (status is None or m.get("status") == status)
        and (project is None or m.get("project") == project)
    ]


def _items(body: str, title: str) -> list[str]:
    text = body.partition(f"## {title}\n")[2].partition("\n\n## ")[0]
    return [line.removeprefix("- [ ] ").removeprefix("- ") for line in text.splitlines() if line.startswith("- ")]


def get_dump(inbox: Path, dump_id: str) -> dict:
    _, meta, body = _read(inbox, dump_id)
    head, _, tail = body.partition("## Transcript\n")
    return {
        **meta,
        "actions": _items(head, "Actions"),
        "ideas": _items(head, "Ideas"),
        "questions": _items(head, "Questions"),
        "transcript": tail.partition("\n\n## Done (")[0].strip(),
        "body": body,
    }


def catch_up(inbox: Path, *, now: datetime | None = None) -> dict:
    """Last 24 h of saved captures, with stored actions from unfinished captures only."""
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(hours=24)
    recent, skipped = [], 0
    for meta in list_dumps(inbox):
        try:
            created = datetime.fromisoformat(meta.get("created", ""))
            if created.tzinfo is None:
                raise ValueError("missing timezone")
        except (TypeError, ValueError):
            skipped += 1
            continue
        if start <= created <= end:
            recent.append((created, meta))
    summaries, actions = [], []
    for _, meta in sorted(recent, key=lambda item: (item[0], item[1]["id"]), reverse=True):
        try:
            original = get_dump(inbox, meta["id"])
        except (DumpError, OSError):
            skipped += 1
            continue
        summaries.append(meta)
        if original.get("status") not in ("new", "claimed"):
            continue
        for text in original["actions"]:
            if len(actions) < 3 and text.strip() and not text.lower().startswith("[x] "):
                actions.append({"id": meta["id"], "created": meta["created"], "source": meta.get("source", ""), "text": text})
    return {"window_start": start.isoformat(), "window_end": end.isoformat(),
            "dumps": summaries, "actions": actions, "skipped": skipped}


def _update(inbox: Path, dump_id: str, change: Callable[[dict, str], str | None]) -> dict:
    inbox = Path(inbox)
    with _lock(inbox):
        path, meta, body = _read(inbox, dump_id)
        body = change(meta, body) or body
        _atomic_write(path, _render(meta, body))
        _write_index(inbox)
    return meta


def claim_dump(inbox: Path, dump_id: str, agent: str) -> dict:
    agent = _one_line(agent)
    if not agent:
        raise DumpError("agent_required")

    def change(meta, _body):
        if meta.get("status") == "done":
            raise DumpError("already_done")
        if meta.get("status") == "claimed" and meta.get("claimed_by") != agent:
            raise DumpError(f"already_claimed_by:{meta.get('claimed_by')}")
        meta["status"], meta["claimed_by"] = "claimed", agent

    return _update(inbox, dump_id, change)


def finish_dump(inbox: Path, dump_id: str, agent: str, note: str = "") -> dict:
    agent = _one_line(agent)
    if not agent:
        raise DumpError("agent_required")

    def change(meta, body):
        if meta.get("status") == "claimed" and meta.get("claimed_by") != agent:
            raise DumpError(f"already_claimed_by:{meta.get('claimed_by')}")
        meta["status"], meta["claimed_by"] = "done", agent
        if _one_line(note):
            return body.rstrip("\n") + f"\n\n## Done ({agent})\n{_one_line(note)}\n"

    return _update(inbox, dump_id, change)


def assign_dump(inbox: Path, dump_id: str, project: str) -> dict:
    project = _one_line(project)
    if not project:
        raise DumpError("project_required")
    return _update(inbox, dump_id, lambda meta, _b: meta.update(project=project))


def _age(created: str) -> str:
    try:
        secs = (datetime.now(timezone.utc) - datetime.fromisoformat(created)).total_seconds()
    except ValueError:
        return "?"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if secs >= size:
            return f"{int(secs // size)}{unit}"
    return "now"


def _write_index(inbox: Path) -> None:
    groups: dict[str, list[dict]] = {}
    for meta in _all(inbox):
        if meta.get("status") != "done":
            groups.setdefault(meta.get("project") or "unassigned", []).append(meta)
    lines = [_HEADER, "## Open dumps\n"]
    if not groups:
        lines.append("(none)\n")
    for name in sorted(groups):
        lines.append(f"### {name}\n")
        for m in groups[name]:  # already newest first
            who = f"claimed by {m['claimed_by']}" if m.get("claimed_by") else m.get("status", "new")
            lines.append(
                f"- `{m['id']}` ({_age(m.get('created', ''))}) {m.get('summary', '')[:120]}"
                f" [{who}]({m['id']}.md)"
            )
        lines.append("")
    _atomic_write(inbox / "INDEX.md", "\n".join(lines))


def load_projects(inbox: Path) -> list[dict]:
    """projects.json in the inbox; a generic starter file is created if missing."""
    path = Path(inbox) / "projects.json"
    if not path.is_file():
        Path(inbox).mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(_STARTER_PROJECTS, indent=2) + "\n")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [p for p in data if isinstance(p, dict) and p.get("name")]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def assign_project(
    inbox: Path,
    transcript: str,
    summary: str = "",
    llm: Callable[[str, str], str] | None = None,
) -> str:
    """Keyword match first (offline), then optional llm(system, user) -> name, else 'unassigned'."""
    try:
        projects = load_projects(inbox)
        text = f"{summary}\n{transcript}".lower()
        best, best_hits = "unassigned", 0
        for p in projects:
            hits = sum(1 for k in p.get("keywords") or [] if str(k).strip() and str(k).lower() in text)
            if hits > best_hits:
                best, best_hits = str(p["name"]), hits
        if best_hits or not llm or not projects:
            return best
        names = [str(p["name"]) for p in projects]
        system = (
            "Pick the single project this note belongs to. Reply with exactly one name from: "
            + ", ".join(names) + ", or the word unassigned. No other text."
        )
        answer = _one_line(llm(system, f"{summary}\n{transcript}"[:4000])).strip("\"'`. ").lower()
        return next((n for n in names if n.lower() == answer), "unassigned")
    except Exception:  # noqa: BLE001 - assignment must never block saving a dump
        return "unassigned"
