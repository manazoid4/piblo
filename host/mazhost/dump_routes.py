"""Dump inbox API. Mounted on the main app, so the global bearer-token dependency applies."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import dumps
from .braindump import structure_braindump
from .config import Settings

log = logging.getLogger("uvicorn.error")


def inbox_dir(cfg: Settings) -> Path:
    return Path(cfg.dumps_dir).expanduser()


def save_safely(cfg: Settings, model_router, transcript: str, structured: dict | None, source: str,
                kind: str = "braindump", project: str | None = None) -> dict:
    """Save + assign a dump. Never raises: a disk problem must not break the device reply."""
    try:
        inbox = inbox_dir(cfg)

        def ask(system: str, user: str) -> str:
            return model_router.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                cfg.default_route,
            )[0]

        ask_llm = ask if model_router is not None else None  # None: keyword match only

        summary = (structured or {}).get("summary", "")
        name = project or dumps.assign_project(inbox, transcript, summary, ask_llm)
        meta = dumps.save_dump(inbox, transcript, structured, source=source, kind=kind, project=name)
        return {"dump_id": meta["id"], "project": meta["project"]}
    except Exception:  # noqa: BLE001
        log.exception("dump save failed")
        return {"dump_id": None, "project": None}


class NewDump(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)
    source: str = Field(default="api", pattern="^(device|flow|web|api)$")
    kind: str = Field(default="braindump", max_length=40)
    project: str | None = Field(default=None, max_length=80)


class Claim(BaseModel):
    agent: str = Field(min_length=1, max_length=60)


class Done(Claim):
    note: str = Field(default="", max_length=2000)


class Assign(BaseModel):
    project: str = Field(min_length=1, max_length=80)


def build_dump_router(cfg: Settings, model_router) -> APIRouter:
    router = APIRouter(prefix="/dumps", tags=["dumps"])
    inbox = inbox_dir(cfg)

    def call(fn, *args):
        try:
            return fn(inbox, *args)
        except dumps.DumpError as error:
            msg = str(error)
            code = 404 if msg == "dump_not_found" else 409 if msg.startswith("already_") else 400
            raise HTTPException(code, msg) from error

    @router.get("")
    def list_(status: str | None = Query(default=None, pattern="^(new|claimed|done)$"),
              project: str | None = None):
        return {"dumps": dumps.list_dumps(inbox, status, project)}

    @router.get("/catch-up")
    def catch_up():
        return call(dumps.catch_up)

    @router.get("/{dump_id}")
    def get(dump_id: str):
        return call(dumps.get_dump, dump_id)

    @router.post("")
    async def add(body: NewDump):
        def work():
            structured, _ = structure_braindump("", body.text)  # deterministic: no LLM wait for typed text
            return save_safely(cfg, model_router, body.text, structured, body.source, body.kind, body.project)

        result = await run_in_threadpool(work)
        if result["dump_id"] is None:
            raise HTTPException(500, "dump_save_failed")
        return result

    @router.post("/{dump_id}/claim")
    def claim(dump_id: str, body: Claim):
        return call(dumps.claim_dump, dump_id, body.agent)

    @router.post("/{dump_id}/done")
    def done(dump_id: str, body: Done):
        return call(dumps.finish_dump, dump_id, body.agent, body.note)

    @router.post("/{dump_id}/assign")
    def assign(dump_id: str, body: Assign):
        return call(dumps.assign_dump, dump_id, body.project)

    return router
