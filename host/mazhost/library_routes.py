"""Library API. Mounted on the main app, so the global bearer-token dependency applies."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import library
from .config import Settings
from .dump_routes import inbox_dir


class Ingest(BaseModel):
    kind: Literal["url", "text", "instagram"]
    url: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=50_000)
    author: str = Field(default="", max_length=200)
    collection: str = Field(default="", max_length=200)
    project: str | None = Field(default=None, max_length=80)


def build_library_router(cfg: Settings, model_router) -> APIRouter:
    router = APIRouter(tags=["library"])
    root = inbox_dir(cfg) / "library"

    def ask(system: str, user: str) -> str:
        return model_router.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], cfg.default_route
        )[0]

    @router.post("/ingest")
    async def ingest(body: Ingest):
        url = body.url.strip()
        if body.kind == "text" and not body.text.strip():
            raise HTTPException(422, "text_required")
        if body.kind != "text" and not url.lower().startswith(("http://", "https://")):
            raise HTTPException(422, "url_must_be_http")
        if body.kind == "instagram" and not library._POST.match(url):
            raise HTTPException(422, "not_an_instagram_post_url")
        return await run_in_threadpool(
            lambda: library.save_item(
                root, kind=body.kind, url="" if body.kind == "text" else url, text=body.text,
                author=body.author, collection=body.collection, project=body.project,
                llm=ask if model_router is not None else None,
            )
        )

    @router.post("/ingest/file")
    async def ingest_file(file: UploadFile):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".zip", ".json"):
            raise HTTPException(415, "zip_or_json_only")
        limit = cfg.max_upload_mb * 1024 * 1024
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as target:
            path, size = Path(target.name), 0
            try:
                while chunk := await file.read(64 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, "file_too_large")
                    target.write(chunk)
            except BaseException:
                target.close()
                path.unlink(missing_ok=True)
                raise
        try:
            return await run_in_threadpool(library.import_instagram_export, root, path)
        finally:
            path.unlink(missing_ok=True)

    @router.post("/library/scan")
    async def scan():
        return await run_in_threadpool(library.scan_drop, root)

    @router.get("/library/search")
    async def search(q: str = Query(default="", max_length=500), project: str | None = None,
                     kind: str | None = Query(default=None, pattern="^(instagram|web|url|text)$"),
                     limit: int = Query(default=10, ge=1, le=100)):
        return {"results": await run_in_threadpool(
            lambda: library.search(root, q, project=project, kind=kind, limit=limit))}

    @router.get("/library/recent")
    async def recent(kind: Literal["instagram", "web", "text"] | None = None,
                     limit: int = Query(default=20, ge=1, le=50)):
        rows = await run_in_threadpool(lambda: library.recent(root, limit=limit, kind=kind))
        # Return the visible source, not local filesystem paths, private export data or raw credentials.
        return {"items": [
            {key: row.get(key) for key in ("id", "kind", "url", "title", "author", "project", "tags", "when")}
            for row in rows
        ]}

    @router.get("/library/stats")
    async def stats():
        return await run_in_threadpool(library.stats, root)

    return router
