"""Piblo device -> Core -> PibloCraft job queue. Nothing is sent until the device says yes.

POST /bridge/job {command, cwd?}  : logs the command, asks for approval on the device (the same
                                    approval the Ask-before-it-acts helpers use), returns at once.
GET  /bridge/job/{id}             : waiting | sending | sent | denied | expired | send_failed | no_receiver
On "allow" Core POSTs {id, command, cwd, requested_by, approval_required: true} to
MAZ_PIBLOCRAFT_BRIDGE_URL (the PibloCraft receiver, docs/bridge-protocol.md in that repo).
Every step is one JSON line in <data dir>/bridge.log, so the command is on record on this side.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

REQUESTER = "piblo-device"
WAIT_S = 70  # a little longer than the approval's own 60 s expiry


class JobIn(BaseModel):
    command: str = Field(min_length=1, max_length=500)
    cwd: str = Field(default="", max_length=260)


class JobBridge:
    def __init__(self, buddy, data_dir: Path, url: str, post=None, wait_s: float = WAIT_S) -> None:
        self.buddy, self.url, self.wait_s = buddy, url, wait_s
        self.post = post or (lambda u, j: httpx.post(u, json=j, timeout=10))
        self.log_path = Path(data_dir) / "bridge.log"
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _log(self, job: dict, event: str, **extra) -> None:
        line = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": job["id"], "event": event,
                "command": job["command"], "cwd": job["cwd"], **extra}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

    def submit(self, command: str, cwd: str = "") -> dict:
        command = command.strip()
        if "\n" in command or "\r" in command or not command:
            raise ValueError("command must be one line")
        job = {"id": uuid.uuid4().hex[:12], "command": command, "cwd": cwd, "status": "waiting"}
        self.jobs[job["id"]] = job
        self._log(job, "requested")
        ask = self.buddy.request("PibloCraft job", command[:200], agent=REQUESTER, idem="bridge-" + job["id"])
        job["approval_id"] = ask["id"]
        threading.Thread(target=self._finish, args=(job,), daemon=True, name=f"bridge-{job['id']}").start()
        return dict(job)

    def _finish(self, job: dict) -> None:
        end = time.monotonic() + self.wait_s
        decision = None
        while decision is None and time.monotonic() < end:
            decision = self.buddy.state(job["approval_id"], 5).get("decision")
        if decision != "allow":
            job["status"] = "denied" if decision == "deny" else "expired"
            self._log(job, "not_sent", decision=decision)
            return
        if not self.url:
            job["status"] = "no_receiver"
            self._log(job, "not_sent", reason="MAZ_PIBLOCRAFT_BRIDGE_URL is empty")
            return
        job["status"] = "sending"
        try:
            r = self.post(self.url, {"id": job["id"], "command": job["command"], "cwd": job["cwd"],
                                     "requested_by": REQUESTER, "approval_required": True})
            r.raise_for_status()
            job["status"] = "sent"
            self._log(job, "sent", http=r.status_code)
        except Exception as error:  # noqa: BLE001 - receiver down: report, never retry silently
            job["status"] = "send_failed"
            self._log(job, "send_failed", error=type(error).__name__)


def build_router(bridge: JobBridge) -> APIRouter:
    router = APIRouter()

    @router.post("/bridge/job")
    def submit(body: JobIn):
        try:
            return bridge.submit(body.command, body.cwd)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/bridge/job/{job_id}")
    def status(job_id: str):
        job = bridge.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job_not_found")
        return job

    return router
