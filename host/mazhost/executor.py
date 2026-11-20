from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from .authority import AuthorityBroker, AuthorityError, Grant
from .config import Settings
from . import private_mode
from .nowindow import no_window


Shell = Literal["powershell", "cmd"]


class ExecutionError(RuntimeError):
    pass


class ElevatedExecutor:
    """Runs user-approved commands and nothing else.

    Possessing the normal MAZ bearer token is not enough. Every command must
    carry a short-lived AuthorityBroker grant that covers the requested scope.
    """

    def __init__(self, settings: Settings, broker: AuthorityBroker) -> None:
        self.settings = settings
        self.broker = broker

    @staticmethod
    def _project_root(project: str) -> Path:
        return Path(project).expanduser().resolve()

    def _cwd_for(self, grant: Grant, cwd: str) -> Path:
        requested = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
        if grant.scope == "project_full":
            root = self._project_root(grant.project)
            if not root.exists() or not root.is_dir():
                raise ExecutionError("approved_project_missing")
            try:
                requested.relative_to(root)
            except ValueError as error:
                raise ExecutionError("cwd_outside_approved_project") from error
        if not requested.exists() or not requested.is_dir():
            raise ExecutionError("cwd_not_found")
        return requested

    @staticmethod
    def _argv(shell: Shell, command: str) -> list[str]:
        if shell == "powershell":
            exe = "powershell.exe" if os.name == "nt" else "pwsh"
            return [exe, "-NoProfile", "-NonInteractive", "-Command", command]
        if shell == "cmd":
            if os.name != "nt":
                raise ExecutionError("cmd_requires_windows")
            return ["cmd.exe", "/d", "/s", "/c", command]
        raise ExecutionError("unsupported_shell")

    def run(
        self,
        *,
        grant_token: str,
        command: str,
        cwd: str = "",
        shell: Shell = "powershell",
        timeout_seconds: int = 120,
        agent: str = "",
        requires_admin: bool = False,
    ) -> dict:
        # Arbitrary shell commands cannot be proved local by parsing their text.
        private_mode.guard_subprocess("shell command")
        if not command.strip():
            raise ExecutionError("command_required")
        required = {"admin"} if requires_admin else {"project_full"}
        try:
            grant = self.broker.verify_grant(grant_token, required=required, agent=agent)
        except AuthorityError as error:
            raise ExecutionError(str(error)) from error

        if grant.scope == "read_only":
            raise ExecutionError("write_or_shell_not_authorized")
        if grant.scope == "project_full" and requires_admin:
            raise ExecutionError("admin_scope_required")

        workdir = self._cwd_for(grant, cwd)
        timeout_seconds = max(1, min(int(timeout_seconds), self.settings.control_command_timeout_seconds))
        argv = self._argv(shell, command)
        started = time.time()
        self.broker.audit_action(
            "command_start",
            {
                "grant_id": grant.grant_id,
                "agent": grant.agent,
                "scope": grant.scope,
                "cwd": str(workdir),
                "shell": shell,
                "command_hash": __import__("hashlib").sha256(command.encode("utf-8")).hexdigest(),
            },
        )
        try:
            private_mode.guard_subprocess("shell command")
            completed = subprocess.run(
                argv,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout_seconds,
                env=os.environ.copy(),
                **no_window(),
            )
        except FileNotFoundError as error:
            raise ExecutionError(f"shell_not_found: {argv[0]}") from error
        except subprocess.TimeoutExpired as error:
            self.broker.audit_action(
                "command_timeout",
                {"grant_id": grant.grant_id, "cwd": str(workdir), "seconds": timeout_seconds},
            )
            raise ExecutionError("command_timeout") from error

        duration_ms = round((time.time() - started) * 1000)
        # Bound returned output so a runaway command cannot turn a phone/Pocket
        # request into an accidental multi-megabyte log transfer.
        stdout = completed.stdout[-self.settings.control_max_output_chars :]
        stderr = completed.stderr[-self.settings.control_max_output_chars :]
        result = {
            "ok": completed.returncode == 0,
            "grant": {
                "grant_id": grant.grant_id,
                "scope": grant.scope,
                "agent": grant.agent,
                "expires_at": grant.expires_at,
            },
            "shell": shell,
            "cwd": str(workdir),
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": (
                len(completed.stdout) > self.settings.control_max_output_chars
                or len(completed.stderr) > self.settings.control_max_output_chars
            ),
        }
        self.broker.audit_action(
            "command_end",
            {
                "grant_id": grant.grant_id,
                "exit_code": completed.returncode,
                "duration_ms": duration_ms,
                "cwd": str(workdir),
            },
        )
        return result
