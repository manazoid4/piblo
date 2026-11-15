"""Reply-voice selection for Fish Audio TTS (free model, public library voices only).

The selected voice persists in <data_dir>/settings.json (never in .env). Previews
are cached on disk per voice so they cost one free Fish call the first time only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import Settings
from . import private_mode

log = logging.getLogger("uvicorn.error")

ADRIAN_ID = "bf322df2096a46f18c579d0baa36f41d"
ID_RE = re.compile(r"^[0-9a-f]{32}$")
PREVIEW_TEXT = "Hi, I'm Piblo."
SEARCH_TTL_S = 600

# Verified against GET https://api.fish.audio/model (public, type=tts, en) on 2026-10-07.
CURATED: list[dict[str, str]] = [
    {"name": "Adrian", "reference_id": ADRIAN_ID, "description": "Steady, deep narrator (default)"},
    {"name": "Sarah", "reference_id": "933563129e564b19a115bedd57b7406a", "description": "Soft, engaged female, conversational"},
    {"name": "Ethan", "reference_id": "536d3a5e000945adb7038665781a4aca", "description": "Curious male explainer, professional"},
    {"name": "Selene", "reference_id": "b347db033a6549378b48d00acb0d06cd", "description": "Calm, meditative female"},
    {"name": "Slax", "reference_id": "c5f56a6cc2ec4fa8920cb4c5889a3fb7", "description": "Clear, crisp male, measured"},
    {"name": "ELITE", "reference_id": "d8a1340984ee4b63ad1ffae27a6a4339", "description": "Confident, energetic male"},
    {"name": "Verity", "reference_id": "711cf3ed00ab441a8f54a45058047b7a", "description": "Friendly, upbeat young male"},
    {"name": "Energetic Male", "reference_id": "802e3bc2b27e49c2995d23ef70e6ac89", "description": "Youthful, clear, professional"},
]


def resolve_data_dir(settings: Settings) -> Path:
    if settings.data_dir:
        return Path(settings.data_dir).expanduser()
    return Path(__file__).resolve().parent.parent / "data"  # host/data in the install dir


class VoiceService:
    def __init__(self, settings: Settings, synth: Callable[[str, Path, str], None] | None = None) -> None:
        self.settings = settings
        self.dir = resolve_data_dir(settings)
        self._lock = threading.Lock()
        self._search_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._warned: set[str] = set()
        self.fallback: Callable[[str], Path] | None = None  # local TTS when Fish is unavailable
        self._synth = synth  # (text, path, reference_id) -> writes wav; set by SpeechOut

    @property
    def default_id(self) -> str:
        return self.settings.tts_voice if ID_RE.match(self.settings.tts_voice) else ADRIAN_ID

    @property
    def _file(self) -> Path:
        return self.dir / "settings.json"

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def warn_once(self, key: str, msg: str, *args: Any) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(msg, *args)

    def current(self) -> str:
        value = self._read().get("voice_reference_id")
        if value is None:
            return self.default_id
        if not isinstance(value, str) or not ID_RE.match(value):
            self.warn_once("bad:" + str(value)[:40], "voice: invalid stored id, using Adrian")
            return ADRIAN_ID
        return value

    def select(self, reference_id: str, name: str = "") -> str:
        ref = reference_id.strip().lower()
        if not ID_RE.match(ref):
            raise ValueError("invalid_reference_id")
        with self._lock:
            data = self._read()
            data["voice_reference_id"] = ref
            if name:
                data["voice_name"] = name[:80]
            else:
                data.pop("voice_name", None)
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._file)
        return ref

    def reject(self, reference_id: str) -> None:
        """Fish refused this id: fall back to Adrian for good, log once."""
        self.warn_once("rej:" + reference_id, "voice: Fish rejected reference_id %s..., falling back to Adrian", reference_id[:8])
        if reference_id != ADRIAN_ID and self.current() == reference_id:
            self.select(ADRIAN_ID, "Adrian")

    def name_of(self, reference_id: str) -> str:
        for v in CURATED:
            if v["reference_id"] == reference_id:
                return v["name"]
        stored = self._read()
        if stored.get("voice_reference_id") == reference_id and stored.get("voice_name"):
            return str(stored["voice_name"])
        return ""

    def listing(self) -> dict[str, Any]:
        cur = self.current()
        voices = [dict(v, current=v["reference_id"] == cur) for v in CURATED]
        if not any(v["current"] for v in voices):
            voices.insert(0, {"name": self.name_of(cur) or "Custom voice", "reference_id": cur,
                              "description": "Picked from search", "current": True})
        return {"ok": True, "current": cur, "current_name": self.name_of(cur), "voices": voices}

    def search(self, q: str) -> list[dict[str, Any]]:
        key = self.settings.fish_api_key
        if not key:
            raise RuntimeError("no_fish_key")
        q = " ".join(q.split())[:60].lower()
        if not q:
            return []
        hit = self._search_cache.get(q)
        if hit and time.monotonic() - hit[0] < SEARCH_TTL_S:
            return hit[1]
        resp = private_mode.get(
            "https://api.fish.audio/model",
            headers={"Authorization": f"Bearer {key}"},
            params={"title": q, "language": "en", "sort_by": "task_count", "page_size": 15, "page_number": 1},
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"fish_http_{resp.status_code}")
        out = []
        for item in resp.json().get("items", []):
            rid = str(item.get("_id", ""))
            if item.get("type") not in (None, "tts") or item.get("visibility", "public") != "public" or not ID_RE.match(rid):
                continue
            out.append({"name": str(item.get("title", ""))[:60], "reference_id": rid,
                        "description": " ".join(str(item.get("description") or "").split())[:120],
                        "tags": [str(t) for t in (item.get("tags") or [])[:5]]})
        self._search_cache[q] = (time.monotonic(), out)
        if len(self._search_cache) > 100:
            self._search_cache.pop(next(iter(self._search_cache)))
        return out

    def preview_path(self, reference_id: str) -> Path:
        ref = reference_id.strip().lower()
        if not ID_RE.match(ref):
            raise ValueError("invalid_reference_id")
        path = self.dir / "voice-previews" / f"{ref}.wav"
        if path.exists() and path.stat().st_size > 44:
            return path
        if self._synth is None:
            raise RuntimeError("tts_unavailable")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        try:
            self._synth(PREVIEW_TEXT, tmp, ref)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return path


SAY_MAX = 300


class SayBody(BaseModel):
    text: str
    voice: str | None = None


class SelectBody(BaseModel):
    reference_id: str
    name: str = ""


def install_voice_routes(app: FastAPI, svc: VoiceService, *, prefix: str = "", dependencies: list | None = None) -> None:
    router = APIRouter(prefix=prefix, dependencies=dependencies or [])

    @router.get("/voices")
    def voices():
        return svc.listing()

    @router.post("/voices/select")
    def voices_select(body: SelectBody):
        try:
            svc.select(body.reference_id, body.name)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return svc.listing()

    @router.get("/voices/search")
    def voices_search(q: str = ""):
        try:
            return {"ok": True, "q": q, "voices": svc.search(q)}
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error
        except httpx.HTTPError as error:
            raise HTTPException(502, "fish_unreachable") from error

    @router.post("/voices/preview")
    def voices_preview(body: SelectBody):
        try:
            path = svc.preview_path(body.reference_id)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except (RuntimeError, httpx.HTTPError) as error:
            raise HTTPException(503, str(error)[:120]) from error
        return FileResponse(path, media_type="audio/wav", filename="voice-preview.wav")

    @router.post("/say")
    def say(body: SayBody, background_tasks: BackgroundTasks):
        text = " ".join(body.text.split())
        if not text:
            raise HTTPException(400, "text_empty")
        if len(text) > SAY_MAX:
            raise HTTPException(400, f"text_too_long_max_{SAY_MAX}")
        voice = (body.voice or "").strip().lower() or svc.current()
        if not ID_RE.match(voice):
            raise HTTPException(400, "invalid_voice")
        if svc._synth is None:
            raise HTTPException(503, "tts_unavailable")
        fd, name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        path = Path(name)
        background_tasks.add_task(path.unlink, missing_ok=True)
        try:
            svc._synth(text, path, voice)  # same single free-model Fish call as previews
        except (RuntimeError, httpx.HTTPError) as error:
            if svc.fallback is None:
                raise HTTPException(503, str(error)[:120]) from error
            try:  # Fish unavailable (no key/offline): local Kokoro, same path as /speak
                local = svc.fallback(text)
            except Exception as local_error:
                raise HTTPException(503, str(local_error)[:120]) from local_error
            background_tasks.add_task(local.unlink, missing_ok=True)
            return FileResponse(local, media_type="audio/wav", filename="say.wav")
        return FileResponse(path, media_type="audio/wav", filename="say.wav")

    app.include_router(router)
