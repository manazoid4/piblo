"""Configured webhook shortcuts with one device approval per invocation."""
from __future__ import annotations

import re
import threading
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from . import private_mode


class ShortcutRun(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class ShortcutPoll(BaseModel):
    grant_token: str = Field(default='', max_length=8192)


class ShortcutAuthorise(ShortcutRun):
    grant_token: str = Field(min_length=1, max_length=8192)


class Shortcuts:
    def __init__(self, actions: dict, buddy, *, executor=None, transport=None):
        self.actions = {}
        for name, target in actions.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9 ]{1,40}", name):
                raise ValueError("invalid_shortcut_name")
            if isinstance(target, dict):
                if set(target) != {"command", "cwd"} or not all(isinstance(target[k], str) for k in target):
                    raise ValueError("invalid_shortcut_command")
                if not target["command"].strip() or len(target["command"]) > 100 or len(target["cwd"]) > 512:
                    raise ValueError("invalid_shortcut_command")
                if any(ord(c) < 32 or ord(c) > 126 for c in target["command"] + target["cwd"]):
                    raise ValueError("invalid_shortcut_command")
                self.actions[name] = dict(target)
                continue
            url = target
            if not isinstance(url, str) or len(url) > 120 or any(ord(c) < 33 or ord(c) > 126 for c in url):
                raise ValueError("invalid_shortcut_url")
            parsed = urlsplit(url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("invalid_shortcut_url")
            self.actions[name] = url
        self.buddy, self.executor, self.transport = buddy, executor, transport
        self._requests = {}
        self._lock = threading.RLock()

    def request(self, name: str) -> dict:
        with self._lock:
            if name not in self.actions:
                raise ValueError("unknown_shortcut")
            target = self.actions[name]
            if isinstance(target, dict):
                target = dict(target)
                summary = f"PowerShell: {target['command']}\nFolder: {target['cwd']}"
            else:
                summary = f"POST {target}\nShortcut: {name}"
            item = self.buddy.request("Run shortcut", summary, project="Piblo shortcuts")
            if len(self._requests) >= 50:
                self._requests.pop(next(iter(self._requests)))
            self._requests[item["id"]] = {"target": target, "status": "pending"}
            return {"id": item["id"], "status": "pending"}

    def poll(self, request_id: str, *, grant_token: str = "") -> dict:
        with self._lock:
            record = self._requests.get(request_id)
            if record is None:
                raise ValueError("unknown_shortcut_request")
            if record["status"] != "pending":
                return {"id": request_id, **{k: v for k, v in record.items() if k != "target"}}
            decision = self.buddy.state(request_id)
            if decision.get("decision") == "allow":
                target = record["target"]
                if isinstance(target, dict) and (not self.executor or not grant_token):
                    return {"id": request_id, "status": "grant_required"}
                # Consume before I/O; a timeout can still mean the action ran.
                record["status"] = "sent"
                if isinstance(target, dict):
                    try:
                        result = self.executor.run(grant_token=grant_token, command=target["command"],
                                                   cwd=target["cwd"], shell="powershell")
                        record.update(status="done", result=result)
                    except Exception:
                        record["status"] = "failed"
                    return {"id": request_id, **{k: v for k, v in record.items() if k != "target"}}
                try:
                    private_mode.guard(target)
                    with private_mode.client(transport=self.transport, timeout=8, trust_env=False,
                                             follow_redirects=False) as client:
                        with client.stream("POST", target) as response:
                            data = bytearray()
                            for chunk in response.iter_bytes(chunk_size=1024):
                                data.extend(chunk)
                                if len(data) >= 4096:
                                    break
                            record.update(status="done", ok=200 <= response.status_code < 300,
                                          status_code=response.status_code,
                                          output=bytes(data[:4096]).decode("utf-8", errors="replace"))
                except (httpx.HTTPError, OSError, private_mode.PrivateModeError):
                    record["status"] = "uncertain"
            elif decision.get("decision") is not None or not decision.get("known"):
                record["status"] = "denied"
            return {"id": request_id, **{k: v for k, v in record.items() if k != "target"}}
