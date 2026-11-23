"""Instant parking and a recent-activity view over Piblo's existing stores."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from . import dumps, library
from .braindump import structure_braindump
from .buddy import AlertIn, Buddy, fold_phone_text
from .config import Settings
from .dump_routes import inbox_dir
from .focus import Focus


class Park(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)


def _timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _item(item_id: str, kind: str, title: str, when, source: str) -> dict:
    return {"id": item_id, "kind": kind, "title": title,
            "when": datetime.fromtimestamp(_timestamp(when), timezone.utc).isoformat(),
            "source": source, "send_to_phone": True}


def _visible(path: Path) -> bool:
    try:
        return not (path.name.startswith(".") or path.is_symlink() or
                    getattr(path.stat(), "st_file_attributes", 0) & 2)
    except OSError:
        return False


def _last_document(roots: list[Path]) -> dict | None:
    newest = None
    for root in roots:
        for folder, dirs, files in os.walk(root, followlinks=False):
            base = Path(folder)
            dirs[:] = [d for d in dirs if d != "node_modules" and _visible(base / d)]
            for name in files:
                path = base / name
                if not _visible(path):
                    continue
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    continue
                if newest is None or modified > newest[0]:
                    newest = (modified, path, root)
    if newest is None:
        return None
    modified, path, root = newest
    item_id = "doc-" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    return _item(item_id, "document", path.name, modified, "project:" + path.relative_to(root).as_posix())


def build_where_router(cfg: Settings, buddy: Buddy, focus: Focus) -> APIRouter:
    router = APIRouter(tags=["where"])
    inbox = inbox_dir(cfg)

    def items() -> list[dict]:
        parked = dumps.list_dumps(inbox)
        # Dump timestamps have second precision; file writes break capture ties.
        parked.sort(key=lambda row: (inbox / f"{row['id']}.md").stat().st_mtime_ns, reverse=True)
        rows = [_item("park-" + row["id"], "park", row["summary"], row["created"], "dumps")
                for row in parked if row.get("kind") == "park"]
        rows += [_item("library-" + row["id"], "library", row["title"], row["when"], row.get("url") or "library")
                 for row in library.recent(inbox / "library", limit=100)]
        sprint = focus.state()
        if sprint["state"] != "idle":
            rows.append(_item("focus-" + str(sprint["started_at"]), "focus", sprint["task"], sprint["started_at"], "focus"))
        now = time.time()
        for alert in buddy.alerts()["alerts"]:
            if alert.get("outcome") == "acked" or (alert.get("expires_at") and _timestamp(alert["expires_at"]) < now):
                continue
            rows.append(_item(alert["id"], "alert", alert["title"], alert["created_at"], alert["source"] or "buddy"))
        doc = _last_document(cfg.project_root_paths)
        if doc:
            rows.append(doc)
        return sorted(rows, key=lambda row: _timestamp(row["when"]), reverse=True)

    router.items = items  # read-only view reused by "carry on where I left off"

    @router.post("/park")
    def park(body: Park):
        text = re.sub(r"^Park:\s*", "", body.text.strip(), flags=re.I).strip()
        if not text:
            raise HTTPException(422, "text_required")
        structured, _ = structure_braindump("", text)
        try:
            saved = dumps.save_dump(inbox, text, structured, source="api", kind="park",
                                    project=dumps.assign_project(inbox, text, structured["summary"]))
        except (OSError, dumps.DumpError) as error:
            raise HTTPException(503, "park_save_failed") from error
        return {**_item("park-" + saved["id"], "park", saved["summary"], saved["created"], "dumps"),
                "dump_id": saved["id"], "text": text}

    @router.get("/where")
    def where(limit: int = Query(default=10, ge=1, le=100)):
        return {"items": items()[:limit]}

    @router.post("/where/{item_id}/phone")
    def phone(item_id: str):
        item = next((row for row in items() if row["id"] == item_id), None)
        if item is None:
            raise HTTPException(404, "where_item_not_found")
        text = item["title"]
        if item["kind"] == "library" and item["source"] != "library":
            text = item["source"]
        if item["kind"] == "document":
            text = item["source"].removeprefix("project:")
        text = fold_phone_text(text)
        if text is None or not 1 <= len(text) <= 260:
            raise HTTPException(422, "item_not_supported_by_phone_text")
        try:
            return buddy.post_alert(AlertIn(source="piblo-where", title=text[:60], body=text[60:]))
        except OverflowError as error:
            raise HTTPException(429, str(error)) from error

    return router
