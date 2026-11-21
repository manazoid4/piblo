"""Run the optional read-only Piblo MCP listener with Core's lifetime."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from . import private_mode
from .config import Settings
from .nowindow import no_window

log = logging.getLogger("uvicorn.error")


class McpService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        cfg = self.settings
        if not cfg.mcp_port or self.process is not None:
            return
        if not cfg.token_configured or cfg.mcp_port == cfg.port:
            log.error("Piblo MCP needs a configured Core token and a distinct port")
            return
        repo = Path(cfg.repo_dir).expanduser() if cfg.repo_dir else Path(__file__).resolve().parents[2]
        entry = (repo / "host" / "mcp" / "dist" / "index.js").resolve()
        if not entry.is_file():
            log.error("Piblo MCP build missing. Build host/mcp or point MAZ_REPO_DIR at the built checkout")
            return
        env = dict(os.environ, PIBLO_TOKEN=cfg.token, PIBLO_URL=f"http://127.0.0.1:{cfg.port}",
                   PIBLO_MCP_PORT=str(cfg.mcp_port))
        # Only this Core is reachable from the managed bridge, even if privacy changes later.
        for name in ("PIBLO_DISPLAY_URL", "NODE_OPTIONS", "NODE_USE_ENV_PROXY"):
            env.pop(name, None)
        try:
            private_mode.guard_subprocess("Piblo MCP", local=True)
            self.process = subprocess.Popen(
                ["node", str(entry), "--http"], env=env, cwd=entry.parent,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, **no_window(),
            )
        except OSError:
            log.error("Piblo MCP could not start. Check that Node.js is on Core's PATH")

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
