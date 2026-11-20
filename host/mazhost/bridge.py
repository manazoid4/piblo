"""Optional private GitHub command bridge for MAZ Core.

This gives ChatGPT/GitHub a narrow way to ask the user's PC for factual state
without Codex. Only private-repo issues titled `[MAZ CORE] ...` are consumed,
and their JSON bodies are dispatched through MazCore's fixed allow-list.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from typing import Any

import httpx

from .config import Settings
from .core import CoreError, MazCore
from . import private_mode
from .nowindow import no_window


class BridgeWorker:
    def __init__(self, settings: Settings, core: MazCore, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.core = core
        self.client = client or private_mode.client(timeout=12)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error = ""
        self._processed = 0

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.bridge_enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "repo": self.settings.bridge_repo if self.settings.bridge_enabled else "",
            "processed": self._processed,
            "last_error": self._last_error,
        }

    def start(self) -> None:
        if not self.settings.bridge_enabled or self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="maz-core-github-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _token(self) -> str:
        if self.settings.github_token:
            return self.settings.github_token
        gh = shutil.which("gh")
        if not gh:
            return ""
        try:
            private_mode.guard_subprocess("GitHub CLI")
            result = subprocess.run(
                [gh, "auth", "token"], capture_output=True, text=True, timeout=5,
                shell=False, encoding="utf-8", errors="replace", **no_window()
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return ""

    def _headers(self) -> dict[str, str]:
        token = self._token()
        if not token:
            raise CoreError("github_bridge_no_token")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _base(self) -> str:
        repo = self.settings.bridge_repo.strip()
        if not repo or "/" not in repo:
            raise CoreError("github_bridge_repo_invalid")
        return f"https://api.github.com/repos/{repo}"

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
                self._last_error = ""
            except (CoreError, private_mode.PrivateModeError, httpx.HTTPError, ValueError, TypeError) as error:
                self._last_error = str(error)[:300]
            self._stop.wait(self.settings.bridge_poll_seconds)

    def poll_once(self) -> int:
        if not self.settings.bridge_enabled or private_mode.enabled():
            return 0
        headers = self._headers()
        response = self.client.get(
            f"{self._base()}/issues",
            headers=headers,
            params={"state": "open", "per_page": 30, "sort": "created", "direction": "asc"},
        )
        response.raise_for_status()
        count = 0
        for issue in response.json():
            if "pull_request" in issue:
                continue
            title = str(issue.get("title", ""))
            if not title.startswith("[MAZ CORE]"):
                continue
            number = int(issue["number"])
            self._process_issue(number, str(issue.get("body") or ""), headers)
            count += 1
        return count

    def _process_issue(self, number: int, body: str, headers: dict[str, str]) -> None:
        try:
            command = json.loads(body)
            if not isinstance(command, dict):
                raise ValueError("body_must_be_json_object")
            result = self.core.dispatch(command)
            envelope = {"ok": True, "command": command.get("command"), "evidence": result}
        except (json.JSONDecodeError, CoreError, ValueError, TypeError) as error:
            envelope = {"ok": False, "error": str(error)}

        rendered = json.dumps(envelope, ensure_ascii=False, indent=2, default=str)
        # GitHub comments have generous limits; keep the bridge evidence compact
        # so one bad build log cannot turn the command queue into a data dump.
        if len(rendered) > 28_000:
            rendered = rendered[:28_000] + "\n...TRUNCATED"
        comment = self.client.post(
            f"{self._base()}/issues/{number}/comments",
            headers=headers,
            json={"body": f"MAZ Core v0.5 result\n\n```json\n{rendered}\n```"},
        )
        comment.raise_for_status()
        closed = self.client.patch(
            f"{self._base()}/issues/{number}",
            headers=headers,
            json={"state": "closed", "state_reason": "completed"},
        )
        closed.raise_for_status()
        self._processed += 1
