"""Core self-update: stage firmware and fast-forward Core's own code from GitHub Releases.

Release contract: tag nod-v{VERSION}-b{build}; assets nod-fw.bin + nod-manifest.json
({version, sha, sha256, size, build, git_sha, core_version, tag}).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from . import fw
from . import private_mode
from .nowindow import no_window
from .nowindow import no_window
from .nowindow import no_window

log = logging.getLogger("uvicorn.error.selfupdate")

REPO = "manazoid4/piblo"  # renamed from manazoid4/nod; GitHub redirects old installs
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
BRANCH = "deploy/local"
FW_ASSET = "nod-fw.bin"
MANIFEST_ASSET = "nod-manifest.json"


def find_repo_dir(configured: str = "") -> Path | None:
    if configured:
        p = Path(configured)
        return p if (p / ".git").exists() else None
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def log_dir() -> Path:
    return Path(os.environ.get("MAZ_LOG_DIR") or Path.cwd() / "logs")


def pick_release(releases: list[dict]) -> dict | None:
    """Newest non-draft release whose tag starts with nod-v."""
    ok = [r for r in releases if isinstance(r, dict) and not r.get("draft")
          and str(r.get("tag_name", "")).startswith("nod-v")]
    ok.sort(key=lambda r: str(r.get("published_at") or r.get("created_at") or ""), reverse=True)
    return ok[0] if ok else None


RESTART_TASK = "nod Core restart"


def restart_helper_args(repo: Path, python: str, new_sha: str, prev_sha: str, port: int,
                        env_file: Path, ld: Path) -> list[str]:
    """Full powershell.exe argv (including exe) for host/restart-core.ps1."""
    return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(repo / "host" / "restart-core.ps1"), "-Repo", str(repo), "-Python", python,
            "-NewSha", new_sha, "-PrevSha", prev_sha, "-Port", str(port),
            "-EnvFile", str(env_file), "-LogDir", str(ld)]


def restart_task_xml(argv: list[str]) -> str:
    """One-shot (no trigger, run on demand) task definition. XML avoids schtasks' 261-char /TR limit and
    gives exact quoting for paths containing spaces."""
    from xml.sax.saxutils import escape
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(argv[0])}</Command>
      <Arguments>{escape(subprocess.list2cmdline(argv[1:]))}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


class SelfUpdater:
    def __init__(
        self,
        *,
        enabled: bool = True,
        interval_s: int = 300,
        repo_dir: str = "",
        github_token: str = "",
        port: int = 8787,
        busy: Callable[[], bool] | None = None,
        http: httpx.Client | None = None,
        restart: Callable[[str, str], None] | None = None,
        install_requirements: bool = True,
    ) -> None:
        self.enabled = enabled
        self.interval_s = max(30, int(interval_s))
        self.repo = find_repo_dir(repo_dir)
        self.port = port
        self._token = github_token  # never logged
        self._busy = busy or (lambda: False)
        self._http = http
        self._restart = restart or self._spawn_restart
        self._pip = install_requirements
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self.running_sha = self._git(["rev-parse", "HEAD"], check=False) or "unknown"
        self.state: dict[str, Any] = {"last_check": None, "result": "never_checked", "error": None,
                                      "latest_tag": None, "latest_git_sha": None, "code": None}

    # ---- status -------------------------------------------------------
    def status(self) -> dict:
        m = fw.manifest() or {}
        return {"enabled": self.enabled, "interval_s": self.interval_s,
                "running_git_sha": self.running_sha, "checkout": str(self.repo) if self.repo else None,
                "checkout_head": self._git(["rev-parse", "HEAD"], check=False),
                "latest_release_tag": self.state["latest_tag"], "latest_git_sha": self.state["latest_git_sha"],
                "last_check": self.state["last_check"], "last_result": self.state["result"],
                "last_error": self.state["error"], "code_update": self.state["code"],
                "staged_fw": {"version": m.get("version"), "sha": m.get("sha"), "sha256": m.get("sha256")} if m else None}

    def brief(self) -> dict:
        return {"enabled": self.enabled, "latest_tag": self.state["latest_tag"],
                "last_check": self.state["last_check"], "result": self.state["result"],
                "error": self.state["error"]}

    # ---- loop ---------------------------------------------------------
    def start(self) -> None:
        if not self.enabled or private_mode.enabled():
            log.info("selfupdate: disabled (MAZ_AUTOUPDATE=0 or private mode)")
            return
        self._task = asyncio.get_running_loop().create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        log.info("selfupdate: loop started, every %ss, checkout=%s", self.interval_s, self.repo)
        await asyncio.sleep(15)
        while True:
            try:
                await asyncio.to_thread(self.check)
            except Exception as e:  # never let the loop die
                log.error("selfupdate: loop error: %s", type(e).__name__)
            await asyncio.sleep(self.interval_s)

    # ---- one check ----------------------------------------------------
    def check(self) -> dict:
        if private_mode.enabled():
            self.state["result"] = "skipped_private_mode"
            self.state["error"] = None
            return self.status()
        if not self._lock.acquire(blocking=False):
            return {**self.status(), "last_result": "already_running"}
        try:
            self.state["last_check"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self.state["error"] = None
            try:
                self.state["result"] = self._check()
            except Exception as e:
                self.state["result"] = "error"
                self.state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                log.error("selfupdate: check failed: %s", self.state["error"])
            return self.status()
        finally:
            self._lock.release()

    def _client(self) -> httpx.Client:
        return self._http or private_mode.client(timeout=30, follow_redirects=True)

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "User-Agent": "nod-core-selfupdate"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _check(self) -> str:
        log.info("selfupdate: checking releases")
        c = self._client()
        r = c.get(RELEASES_URL, headers=self._headers())
        r.raise_for_status()
        rel = pick_release(r.json())
        if not rel:
            log.info("selfupdate: no nod-v release found")
            return "no_release"
        tag = rel["tag_name"]
        assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets", [])}
        if MANIFEST_ASSET not in assets or FW_ASSET not in assets:
            raise RuntimeError(f"release {tag} missing assets")
        mr = c.get(assets[MANIFEST_ASSET], headers={"User-Agent": "nod-core-selfupdate"})
        mr.raise_for_status()
        m = mr.json()
        for k in ("version", "sha256", "size", "git_sha"):
            if k not in m:
                raise RuntimeError(f"release {tag} manifest lacks {k}")
        self.state["latest_tag"] = tag
        self.state["latest_git_sha"] = m["git_sha"]
        log.info("selfupdate: latest release %s fw=%s core=%s", tag, m["version"], m.get("core_version"))
        fw_result = self._stage_fw(c, m, assets[FW_ASSET])
        return f"{fw_result}; {self._update_code(m)}"

    # ---- firmware -----------------------------------------------------
    def _stage_fw(self, c: httpx.Client, m: dict, url: str) -> str:
        cur = fw.manifest() or {}
        if cur.get("sha256") == m["sha256"]:
            log.info("selfupdate: fw already staged (%s)", m["sha256"][:12])
            return "fw_current"
        log.info("selfupdate: downloading fw %s (%s bytes)", m["version"], m["size"])
        d = fw.fw_dir()
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".part")
        mtmp = d / "manifest.json.part"
        h = hashlib.sha256()
        n = 0
        try:
            with os.fdopen(fd, "wb") as f, c.stream("GET", url, headers={"User-Agent": "nod-core-selfupdate"}) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes():
                    f.write(chunk)
                    h.update(chunk)
                    n += len(chunk)
            if n != int(m["size"]) or h.hexdigest() != m["sha256"]:
                log.error("selfupdate: fw verify failed (size %s/%s) - not staged", n, m["size"])
                raise RuntimeError("fw_verify_failed")
            keep = {k: m[k] for k in ("version", "sha", "sha256", "size") if k in m}
            mtmp.write_text(json.dumps(keep), encoding="utf-8")
            os.replace(tmp, d / "latest.bin")
            os.replace(mtmp, d / "manifest.json")
        finally:
            for leftover in (tmp, mtmp):
                if os.path.exists(leftover):
                    os.unlink(leftover)
        log.info("selfupdate: staged fw %s sha=%s", m["version"], m.get("sha"))
        return "fw_staged"

    # ---- code ---------------------------------------------------------
    def _git(self, args: list[str], check: bool = True, timeout: int = 120) -> str:
        if not self.repo:
            return ""
        # Only these exact status queries are local; hooks/builds/fetch are not.
        private_mode.guard_subprocess("self-update git", local=args in (
            ["rev-parse", "HEAD"], ["status", "--porcelain", "--untracked-files=no"],
        ))
        command = ["git", *args]
        if private_mode.enabled():
            command = ["git", "--no-pager", "-c", "core.fsmonitor=false", *args]
        p = subprocess.run(command, cwd=self.repo, capture_output=True, text=True, timeout=timeout, **no_window())
        if check and p.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {p.stderr.strip()[:200]}")
        return p.stdout.strip() if p.returncode == 0 else ""

    def _bad_shas(self) -> set[str]:
        try:
            return set((log_dir() / "selfupdate-bad.txt").read_text().split())
        except OSError:
            return set()

    def _update_code(self, m: dict) -> str:
        private_mode.guard_subprocess("self-update")
        target = str(m["git_sha"]).strip().lower()
        if not self.repo:
            return "code_no_checkout"
        head = self._git(["rev-parse", "HEAD"])
        if head == target:
            self.state["code"] = "current" if head == self.running_sha else "restart_pending"
            return "code_current" if head == self.running_sha else "code_restart_pending"
        if target in self._bad_shas():
            log.warning("selfupdate: %s previously rolled back, skipping", target[:10])
            return "code_skipped_bad_sha"
        if self._busy():
            log.info("selfupdate: busy (call/turn in flight), deferring code update")
            return "code_deferred_busy"
        if self._git(["status", "--porcelain", "--untracked-files=no"]):
            log.warning("selfupdate: checkout dirty, skipping code update")
            self.state["code"] = "skipped_dirty"
            return "code_skipped_dirty"
        if not self._git(["ls-remote", "--heads", "origin", BRANCH], check=False, timeout=60):
            # The checkout's origin has no deploy branch (e.g. a plain clone of main):
            # code self-update is simply not set up here. Skip quietly, don't error.
            if self.state["code"] != "skipped_no_deploy_branch":
                log.info("selfupdate: origin has no %s branch; code self-update off for this checkout", BRANCH)
            self.state["code"] = "skipped_no_deploy_branch"
            return "code_skipped_no_deploy_branch"
        self._git(["fetch", "origin", BRANCH], timeout=180)
        p = subprocess.run(["git", "merge-base", "--is-ancestor", target, f"origin/{BRANCH}"],
                           cwd=self.repo, capture_output=True, **no_window())
        if p.returncode != 0:
            log.warning("selfupdate: %s not on origin/%s, skipping", target[:10], BRANCH)
            self.state["code"] = "skipped_not_on_branch"
            return "code_skipped_not_on_branch"
        log.info("selfupdate: updating code %s -> %s", head[:10], target[:10])
        self._git(["checkout", "--detach", target])
        if self._pip and not self._run_pip():
            log.error("selfupdate: pip failed, reverting to %s", head[:10])
            self._git(["checkout", "--detach", head], check=False)
            self.state["code"] = "pip_failed"
            raise RuntimeError("pip_install_failed")
        self.state["code"] = "restarting"
        log.info("selfupdate: restarting Core into %s", target[:10])
        self._restart(target, head)
        return "code_updated_restarting"

    def _python(self) -> str:
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
            return str(exe.with_name("python.exe"))
        return str(exe)

    def _run_pip(self) -> bool:
        private_mode.guard_subprocess("self-update pip")
        log.info("selfupdate: pip install -r host/requirements.txt")
        p = subprocess.run([self._python(), "-m", "pip", "install", "-r", str(self.repo / "host" / "requirements.txt")],
                           cwd=self.repo, capture_output=True, text=True, timeout=1500, **no_window())
        if p.returncode != 0:
            tail = (p.stdout[-300:] + p.stderr[-300:]).replace("\n", " | ")
            log.error("selfupdate: pip failed: %s", tail)
        return p.returncode == 0

    def _spawn_restart(self, new_sha: str, prev_sha: str) -> None:
        private_mode.guard_subprocess("self-update restart")
        if os.name != "nt":
            log.info("selfupdate: restart required (non-Windows); new code %s on disk", new_sha[:10])
            return
        ld = log_dir()
        ld.mkdir(parents=True, exist_ok=True)
        helper_log = ld / "selfupdate.log"
        offset = helper_log.stat().st_size if helper_log.exists() else 0
        args = restart_helper_args(self.repo, self._python(), new_sha, prev_sha, self.port,
                                   Path.cwd() / ".env", ld)
        # Run the helper from its OWN one-shot scheduled task, not as a child of Core. A child dies with
        # the "nod Core" task job (which the helper itself ends), and a DETACHED_PROCESS powershell launched
        # from windowless pythonw never executes its script at all (reproduced).
        xml = ld / "restart-task.xml"
        xml.write_text(restart_task_xml(args), encoding="utf-16")
        flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        for cmd in (["schtasks", "/Create", "/TN", RESTART_TASK, "/XML", str(xml), "/F"],
                    ["schtasks", "/Run", "/TN", RESTART_TASK]):
            p = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags, timeout=30)
            if p.returncode != 0:
                log.error("selfupdate: restart helper task failed (%s): %s", cmd[1], (p.stdout + p.stderr).strip())
                return
        log.info("selfupdate: started restart helper task %r", RESTART_TASK)
        marker = f"restarting into {new_sha}"
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with open(helper_log, "rb") as f:
                    f.seek(offset)
                    tail = f.read().decode("ascii", "replace")
            except OSError:
                tail = ""
            for line in tail.splitlines():
                if marker in line:
                    log.info("selfupdate: restart helper confirmed running: %s", line.strip())
                    return
            time.sleep(0.5)
        log.error("selfupdate: restart helper did NOT start within 15 s (no line in %s); Core stays on old code",
                  helper_log)
