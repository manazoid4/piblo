"""Piblo Maker: printer observations and individually approved safe actions."""
from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import private_mode
from .accessories import Accessories
from .buddy import AlertIn, Buddy
from .config import Settings
from .voices import resolve_data_dir
from .nowindow import no_window


class PrinterSource(Protocol):
    def status(self) -> dict: ...


def number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def state_name(text: str, progress=None, nozzle=None) -> str:
    text = text.lower()
    if any(word in text for word in ("offline", "closed", "unavailable", "not ready")):
        return "offline"
    if any(word in text for word in ("error", "failed")):
        return "error"
    if "paus" in text:
        return "paused"
    if "heat" in text:
        return "heating"
    if "printing" in text:
        return "printing"
    if "finish" in text or "complete" in text or progress == 100:
        return "finished"
    if text in ("operational", "idle", "ready"):
        nozzle = nozzle or {}
        actual, target = number(nozzle.get("actual")), number(nozzle.get("target"))
        return "heating" if actual is not None and target is not None and target > actual + 3 else "idle"
    return "error"


class EnderForgeSource:
    def __init__(self, command: str):
        self.command = [arg.strip('"') for arg in shlex.split(command, posix=os.name != "nt")]

    def _read(self, operation: str) -> dict:
        if operation not in ("status", "next"):
            raise ValueError("Unsupported printer observation")
        private_mode.guard_subprocess("EnderForge observation", local=True)
        result = subprocess.run([*self.command, operation, "--json"], capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=25, check=False, **no_window())
        if result.returncode == 3:
            return {"unavailable": True}
        if result.returncode != 0:
            raise ValueError("Printer tool could not read the printer")
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("Invalid printer observation")
        return data

    def status(self) -> dict:
        try:
            data = self._read("status")
            if data.get("unavailable"):
                return {"state": "offline"}
            nozzle = data.get("nozzle") or {}
            if not isinstance(nozzle, dict) or (data.get("job") is not None and not isinstance(data["job"], str)):
                return {"state": "error"}
            return {"state": state_name(data.get("connection", ""), data.get("progress"), nozzle),
                    "job": data.get("job"), "progress": number(data.get("progress")),
                    "nozzle": nozzle, "bed": data.get("bed") or {}}
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
            return {"state": "error"}

    def next_step(self) -> dict:
        try:
            data = self._read("next")
            if data.get("unavailable"):
                return {"text": "Can't reach the printer", "next": None}
            step = data.get("next")
            if step is None:
                return {"text": "Guided steps complete. Continue in EnderForge on your computer.", "next": None}
            return {"text": f"Next: {step['title']}. Your part: {step['you']}. Continue in EnderForge on your computer.",
                    "next": {key: step[key] for key in ("step", "title", "you")}}
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            return {"text": "Can't read the next printer step. Check EnderForge on your computer.", "next": None}


class OctoPrintSource:
    def __init__(self, url: str, key_file: str, client: httpx.Client | None = None):
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Use an OctoPrint HTTP URL without embedded credentials")
        self.url, self.key_file, self.client = url.rstrip("/"), Path(key_file).expanduser(), client

    def _request(self, method: str, path: str, **kwargs):
        key = self.key_file.read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError("Missing printer key")
        headers = {"X-Api-Key": key}
        private_mode.guard(self.url + path)
        if self.client is not None:
            result = self.client.request(method, self.url + path, headers=headers, **kwargs)
        else:
            with private_mode.client(timeout=8, trust_env=False, follow_redirects=False) as client:
                result = client.request(method, self.url + path, headers=headers, **kwargs)
        result.raise_for_status()
        return result

    def status(self) -> dict:
        try:
            printer = self._request("GET", "/api/printer").json()
            job = self._request("GET", "/api/job").json()
            file = job["job"]["file"]
            progress = job.get("progress") or {}
            nozzle = (printer.get("temperature") or {}).get("tool0") or {}
            percent = number(progress.get("completion"))
            return {"state": state_name(job.get("state") or printer["state"]["text"], percent, nozzle),
                    "job": file.get("name"), "job_key": [file.get(k) for k in ("name", "path", "origin", "date")],
                    "progress": percent, "remaining_s": number(progress.get("printTimeLeft")),
                    "elapsed_s": number(progress.get("printTime")), "nozzle": nozzle,
                    "bed": (printer.get("temperature") or {}).get("bed") or {}}
        except (OSError, httpx.HTTPError):
            return {"state": "offline"}
        except (ValueError, KeyError, TypeError, AttributeError):
            return {"state": "error"}

    def send(self, action: str) -> None:
        # Keep the outbound vocabulary closed, even for internal callers.
        bodies = {"pause": {"command": "pause", "action": "pause"}, "cancel": {"command": "cancel"}}
        if action not in bodies:
            raise ValueError("Unsupported printer action")
        self._request("POST", "/api/job", json=bodies[action])


class FakePrinterSource:
    def __init__(self, value: dict):
        self.value = value

    def status(self) -> dict:
        return dict(self.value)


class Maker:
    def __init__(self, source: PrinterSource, buddy: Buddy, actions: OctoPrintSource | None = None):
        self.source, self.buddy, self.actions = source, buddy, actions
        self._lock = threading.RLock()
        self._requests: dict[str, dict] = {}
        self._active = False
        self._job = None
        self._cycle = ""
        self._announced: set[str] = set()

    def printer(self) -> dict:
        raw = self.source.status()
        if raw["state"] in ("offline", "error") and self.actions:
            raw = self.actions.status()
        state = raw["state"]
        name = str(raw.get("job") or "unnamed print")[:120]
        text = {"idle": "Printer idle", "heating": "Printer heating", "offline": "Can't reach the printer",
                "error": "Printer error", "finished": f"Print finished: {name}", "paused": f"Paused: {name}"}.get(state)
        if state == "printing":
            text = f"Printing: {name}"
            if (percent := number(raw.get("progress"))) is not None:
                text += f", {max(0, min(100, percent)):.0f}%"
            if (seconds := number(raw.get("remaining_s"))) is not None:
                text += f", about {max(0, math.ceil(seconds / 60))} min left"
        nozzle = raw.get("nozzle") or {}
        actual, target = number(nozzle.get("actual")), number(nozzle.get("target"))
        nozzle_text = f"Nozzle {actual:.0f} of {target:.0f} °C" if actual is not None and target is not None else ""
        return {"state": state, "text": text or "Printer error", "nozzle_text": nozzle_text, "raw": raw}

    def action(self, action: str, request_id: str | None = None) -> dict:
        if action not in ("pause", "cancel"):
            raise ValueError("Unsupported printer action")
        if not self.actions:
            raise ValueError("Configure OctoPrint on the paired computer for Pause and Cancel")
        with self._lock:
            if request_id is None:
                snapshot = self.actions.status()
                if snapshot["state"] not in ("printing", "paused", "heating") or not snapshot.get("job"):
                    raise ValueError("No reachable active print")
                # Buddy retains 50 requests; retain the same bounded number of action records.
                if len(self._requests) >= 50:
                    self._requests.pop(next(iter(self._requests)))
                summary = f"{action.capitalize()} the print '{str(snapshot['job'])[:120]}'?"
                item = self.buddy.request(f"Piblo printer {action}", summary, category="other", project="Piblo Maker")
                request_id = item["id"]
                self._requests[request_id] = {"action": action, "snapshot": snapshot, "status": "pending"}
            record = self._requests.get(request_id)
            if record is None or record["action"] != action:
                raise ValueError("Unknown printer approval")
            if record["status"] != "pending":
                return {"id": request_id, "status": record["status"]}
            decision = self.buddy.state(request_id).get("decision")
            if decision == "allow":
                current, previous = self.actions.status(), record["snapshot"]
                stale = (current["state"] not in ("printing", "paused", "heating")
                         or current.get("job_key") != previous.get("job_key")
                         or current.get("job") != previous.get("job"))
                for field in ("progress", "elapsed_s"):
                    before, after = number(previous.get(field)), number(current.get(field))
                    stale |= before is not None and after is not None and after < before
                if stale:
                    record["status"] = "stale"
                elif self.buddy.state(request_id).get("decision") != "allow":
                    record["status"] = "denied"
                else:
                    # Consume before I/O: an uncertain response must never cause a retry.
                    record["status"] = "sent"
                    try:
                        self.actions.send(action)
                    except (OSError, httpx.HTTPError, ValueError):
                        record["status"] = "uncertain"
            elif decision is not None or not self.buddy.state(request_id).get("known"):
                record["status"] = "denied"
            return {"id": request_id, "status": record["status"]}

    def poll(self) -> dict:
        with self._lock:
            decisions = [self.action(row["action"], rid) for rid, row in list(self._requests.items())
                         if row["status"] == "pending"]
            view = self.printer()
            raw, state = view["raw"], view["state"]
            alerts = []
            if state in ("printing", "paused"):
                if not self._active or raw.get("job") != self._job:
                    self._cycle, self._announced = uuid.uuid4().hex, set()
                self._active, self._job = True, raw.get("job")
            elif self._active:
                kind = {"finished": "finished", "error": "failed", "offline": "offline"}.get(state)
                if kind and kind not in self._announced:
                    title = {"finished": "Print finished", "failed": "Print needs attention", "offline": "Printer went offline"}[kind]
                    self.buddy.post_alert(AlertIn(source="maker", title=title, body=f"{self._job or 'Your print'}: {view['text']}"[:200],
                        idempotency_key=f"maker-{self._cycle}-{kind}"))
                    self._announced.add(kind)
                    alerts.append(kind)
                if state in ("finished", "error", "idle"):
                    self._active = False
            return {**view, "alerts": alerts, "actions": decisions}


class ActionIn(BaseModel):
    request_id: str | None = Field(default=None, max_length=80)


class MakeIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


def install_maker_routes(api: FastAPI, cfg: Settings, buddy: Buddy) -> Maker:
    source = EnderForgeSource(cfg.maker_enderforge_cmd)
    octo = OctoPrintSource(cfg.maker_octoprint_url, cfg.maker_octoprint_key_file) if cfg.maker_octoprint_url else None
    maker = Maker(source, buddy, octo)
    accessories = Accessories(resolve_data_dir(cfg))

    def enabled():
        if not cfg.maker_enderforge_cmd.strip():
            raise HTTPException(503, "Piblo Maker is off. Configure EnderForge on the paired computer.")

    @api.get("/maker/printer")
    def printer():
        enabled()
        return maker.printer()

    @api.get("/maker/next")
    def next_step():
        enabled()
        return source.next_step()

    def action(action_name: str, body: ActionIn | None):
        enabled()
        try:
            return maker.action(action_name, body.request_id if body else None)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @api.post("/maker/pause")
    def pause(body: ActionIn | None = None):
        return action("pause", body)

    @api.post("/maker/cancel")
    def cancel(body: ActionIn | None = None):
        return action("cancel", body)

    @api.post("/maker/poll")
    def poll():
        enabled()
        return maker.poll()

    @api.get("/maker/accessories")
    def library():
        enabled()
        return accessories.library()

    @api.post("/maker/accessories/make")
    def make(body: MakeIn):
        enabled()
        try:
            return accessories.make(body.text)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return maker
