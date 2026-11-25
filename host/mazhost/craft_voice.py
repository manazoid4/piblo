"""Explicit, single-use local transcription for a Craft client and its device."""

import secrets
import math
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


def build_craft_voice_router(security, transcribe_local, *, clock=time.monotonic):
    router = APIRouter(prefix="/craft/voice", tags=["craft voice"])
    tickets = {}
    lock = threading.Lock()
    ttl = 60

    def prune():
        now = clock()
        for key in [key for key, item in tickets.items() if item["expires"] <= now]:
            del tickets[key]

    def find(key):
        prune()
        item = tickets.get(key)
        if item is None:
            raise HTTPException(404, "capture_not_found")
        return item

    @router.post("/arm")
    def arm():
        with lock:
            prune()
            if len(tickets) >= 64:
                raise HTTPException(429, "too_many_captures")
            key = secrets.token_urlsafe(32)
            tickets[key] = {"expires": clock() + ttl, "state": "waiting"}
        return {"capture_id": key, "expires_in": ttl}

    @router.post("/claim")
    def claim():
        with lock:
            prune()
            available = [(key, item) for key, item in tickets.items()
                         if item["state"] == "waiting" and not item.get("claimed")]
            if not available:
                raise HTTPException(404, "capture_not_found")
            if len(available) != 1:
                raise HTTPException(409, "multiple_captures")
            key, item = available[0]
            item["claimed"] = True
            return {"capture_id": key, "expires_in": max(0, math.ceil(item["expires"] - clock()))}

    @router.get("")
    def read(x_craft_capture: str = Header(default="")):
        with lock:
            item = find(x_craft_capture)
            if item["state"] != "ready":
                return {"status": "waiting"}
            del tickets[x_craft_capture]
            return {"status": "ready", "text": item["text"]}

    @router.delete("", status_code=204)
    def cancel(x_craft_capture: str = Header(default="")):
        with lock:
            find(x_craft_capture)
            del tickets[x_craft_capture]
        return Response(status_code=204)

    @router.post("/audio")
    async def audio(request: Request, x_craft_capture: str = Header(default="")):
        with lock:
            item = find(x_craft_capture)
            if item["state"] != "waiting":
                raise HTTPException(409, "capture_already_submitted")
            item["state"] = "processing"
        path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                path = Path(tmp.name)
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > security.settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, "audio_too_large")
                    tmp.write(chunk)
            security.validate_upload(path, size)
            text = await run_in_threadpool(transcribe_local, path)
            if not text.strip():
                text = ""
            with lock:
                current = find(x_craft_capture)
                if current is not item:
                    raise HTTPException(404, "capture_not_found")
                item.update(state="ready", text=text)
            if not text:
                return JSONResponse(status_code=422, content={"detail": "No speech detected. Try again."})
            return {"status": "ready"}
        except Exception:
            with lock:
                if tickets.get(x_craft_capture) is item:
                    del tickets[x_craft_capture]
            raise
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    return router
