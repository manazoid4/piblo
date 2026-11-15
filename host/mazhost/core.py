"""MAZ Core: factual local-PC tools for Piblo and the private web console.

Core exposes a deliberately small allow-list. It can inspect projects and run
normal test/build/git maintenance commands, but there is no generic remote
shell endpoint. Expensive repo summaries are cached so a dashboard refresh or
ordinary chat turn never spawns Git across every local repository.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .security import is_lan_ip
from . import private_mode


PROJECT_MARKERS = (
    ".git",
    "platformio.ini",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
)
TEXT_EXTENSIONS = {
    ".md", ".txt", ".py", ".cpp", ".c", ".h", ".hpp", ".ino", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".ts", ".tsx", ".js", ".mjs", ".css",
    ".html", ".ps1", ".sh",
}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", ".pio", "dist", "build", ".next"}
SECRET_NAMES = {
    ".npmrc", ".pypirc", "credentials.json", "credentials.yml", "credentials.yaml",
    "secrets.json", "secrets.yml", "secrets.yaml", "id_rsa", "id_ed25519",
}
SECRET_EXTENSIONS = {".key", ".pem", ".p12", ".pfx", ".kdbx"}
SUMMARY_TTL_SECONDS = 30.0


_PC_WORDS = re.compile(r"focus|priorit|work|next|project|repo|code|git|branch|commit|build|folder|file|pc\b|computer|laptop|model|ollama|llama|core", re.I)


class CoreError(RuntimeError):
    pass


class MazCore:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or private_mode.client(timeout=12)
        self.device_ip: str | None = None
        self._cache_lock = threading.Lock()
        self._summary_cache: list[dict[str, Any]] = []
        self._refreshing = False
        self._dirty = False
        self._summary_cache_at = 0.0

    # ---------------------------------------------------------------- system
    def status(self) -> dict[str, Any]:
        # Counting paths is cheap. Do not run status/log in every repo merely to
        # render /health or the Maz Works dashboard.
        project_count = len(self._project_paths(limit=80))
        return {
            "ok": True,
            "version": "0.5.0",
            "hostname": socket.gethostname(),
            "os": platform.platform(),
            "python": platform.python_version(),
            "local_engine": self.settings.local_engine,
            "ollama_model": self._selected_local_model(),
            "ollama": self._local_runtime_status(),
            "git": bool(shutil.which("git")),
            "platformio": bool(shutil.which("pio") or shutil.which("platformio")),
            "project_roots": [str(path) for path in self.settings.project_root_paths],
            "project_count": project_count,
            "obsidian": str(self._obsidian_root() or ""),
            "bridge": self.settings.bridge_enabled,
            "bridge_repo": self.settings.bridge_repo if self.settings.bridge_enabled else "",
            "cardputer_url": self.settings.cardputer_url,
        }

    def _selected_local_model(self) -> str:
        if self.settings.local_engine == "llamacpp":
            return self.settings.llamacpp_model
        return self.settings.ollama_model

    def _local_runtime_status(self) -> dict[str, Any]:
        # The key stays `ollama` because Pocket firmware and the dashboard read
        # it by that name. What it describes is whichever local engine is
        # configured; `local_engine` alongside it says which one answered.
        if self.settings.local_engine == "llamacpp":
            return self._llamacpp_status()
        try:
            response = self.client.get(
                f"{self.settings.ollama_url.rstrip('/')}/api/tags", timeout=2
            )
            response.raise_for_status()
            names = [item.get("name", "") for item in response.json().get("models", [])]
            return {
                "online": True,
                "selected_installed": self.settings.ollama_model in names,
                "models": names[:30],
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return {"online": False, "selected_installed": False, "models": []}

    def _llamacpp_status(self) -> dict[str, Any]:
        base = self.settings.llamacpp_url.rstrip("/")
        try:
            response = self.client.get(f"{base}/health", timeout=2)
            response.raise_for_status()
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return {"online": False, "selected_installed": False, "models": []}
        names: list[str] = []
        try:
            listed = self.client.get(f"{base}/v1/models", timeout=2)
            listed.raise_for_status()
            names = [str(item.get("id", "")) for item in listed.json().get("data", [])]
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            names = []
        # A healthy llama-server is serving the one model it was launched with,
        # so being online is the honest answer to "is the selection loaded".
        return {"online": True, "selected_installed": True, "models": names[:30]}

    # --------------------------------------------------------------- projects
    def _looks_like_project(self, path: Path) -> bool:
        return path.is_dir() and any((path / marker).exists() for marker in PROJECT_MARKERS)

    def _project_paths(self, limit: int = 80) -> list[Path]:
        found: list[Path] = []
        seen: set[str] = set()
        for root in self.settings.project_root_paths:
            if not root.exists() or not root.is_dir():
                continue
            candidates = [root]
            try:
                children = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
            except OSError:
                children = []
            candidates.extend(children)
            # One extra level catches common Desktop/Projects/workspace layouts
            # without turning discovery into an unbounded filesystem crawl.
            for child in children[:80]:
                if self._looks_like_project(child):
                    continue
                try:
                    candidates.extend(
                        p for p in child.iterdir()
                        if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP_DIRS
                    )
                except OSError:
                    pass
            for path in candidates:
                if not self._looks_like_project(path):
                    continue
                try:
                    key = str(path.resolve()).lower()
                except OSError:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                found.append(path)
                if len(found) >= limit:
                    return sorted(found, key=lambda p: p.name.lower())
        return sorted(found, key=lambda p: p.name.lower())

    def _git(self, path: Path, *args: str, timeout: int = 12) -> dict[str, Any]:
        if not shutil.which("git"):
            return {"ok": False, "error": "git_not_installed", "output": ""}
        return self._run(["git", *args], path, timeout=timeout)

    def _project_summary(self, path: Path) -> dict[str, Any]:
        branch = ""
        dirty = 0
        last = ""
        if (path / ".git").exists():
            branch_result = self._git(path, "branch", "--show-current")
            if branch_result["ok"]:
                raw = branch_result["output"].strip()
                branch = raw.splitlines()[0] if raw else "detached"
            status_result = self._git(path, "status", "--porcelain")
            if status_result["ok"]:
                dirty = len([line for line in status_result["output"].splitlines() if line.strip()])
            last_result = self._git(path, "log", "-1", "--pretty=%h %s")
            if last_result["ok"]:
                last = last_result["output"].strip()[:180]
        return {
            "name": path.name,
            "path": str(path),
            "branch": branch,
            "dirty": dirty,
            "last_commit": last,
            "kind": self._project_kind(path),
        }

    def _project_kind(self, path: Path) -> str:
        if (path / "platformio.ini").exists():
            return "platformio"
        if (path / "package.json").exists():
            return "node"
        if (path / "pyproject.toml").exists() or (path / "requirements.txt").exists():
            return "python"
        if (path / "Cargo.toml").exists():
            return "rust"
        if (path / "go.mod").exists():
            return "go"
        return "git"

    def _invalidate_project_cache(self) -> None:
        # A git pull/fetch/status just changed branch counts: mark stale and rescan in
        # the background so the next read is fresh without blocking this caller.
        with self._cache_lock:
            self._summary_cache_at = 0.0
            self._dirty = True
        self._kick_refresh()

    def _refresh_projects(self) -> None:
        try:
            from concurrent.futures import ThreadPoolExecutor
            while True:
                with self._cache_lock:
                    self._dirty = False
                paths = self._project_paths(limit=80)
                with ThreadPoolExecutor(max_workers=8) as pool:  # git calls are subprocess-bound
                    summaries = list(pool.map(self._project_summary, paths))
                with self._cache_lock:
                    if self._dirty:  # invalidated mid-scan: this result may predate the change
                        continue
                    self._summary_cache = summaries
                    self._summary_cache_at = time.monotonic()
                    self._refreshing = False  # same lock block as the write: no window where a fresh cache is "refreshing"
                    return
        finally:
            with self._cache_lock:
                self._refreshing = False

    def _kick_refresh(self) -> None:
        with self._cache_lock:
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self._refresh_projects, name="projects-refresh", daemon=True).start()

    def projects(self, limit: int = 60) -> list[dict[str, Any]]:
        """Stale-while-revalidate: serve the cached scan at once, refresh in the background."""
        with self._cache_lock:
            cached = self._summary_cache
            fresh = time.monotonic() - self._summary_cache_at < SUMMARY_TTL_SECONDS
        if cached:
            if not fresh:
                self._kick_refresh()
            return [dict(item) for item in cached[:limit]]
        self._kick_refresh()  # first ever call: wait for the scan
        while True:
            with self._cache_lock:
                if self._summary_cache or not self._refreshing:
                    return [dict(item) for item in self._summary_cache[:limit]]
            time.sleep(0.05)

    def _find_project(self, name: str) -> Path:
        wanted = name.strip().lower()
        if not wanted:
            raise CoreError("project_required")
        for path in self._project_paths():
            if path.name.lower() == wanted:
                return path
        raise CoreError(f"project_not_found:{name}")

    def project(self, name: str) -> dict[str, Any]:
        path = self._find_project(name)
        result = self._project_summary(path)
        if (path / ".git").exists():
            status = self._git(path, "status", "--short", "--branch")
            log = self._git(path, "log", "-5", "--pretty=%h %ad %s", "--date=short")
            result["git_status"] = status["output"][:6000]
            result["recent_commits"] = log["output"][:6000]
        result["files"] = self._top_files(path)
        return result

    def _top_files(self, path: Path) -> list[str]:
        try:
            return sorted(
                p.name for p in path.iterdir()
                if p.is_file() and not self._secret_name(p)
            )[:40]
        except OSError:
            return []

    # ------------------------------------------------------------- safe files
    @staticmethod
    def _secret_name(path: Path) -> bool:
        lower = path.name.lower()
        return (
            lower == ".env"
            or lower.startswith(".env.")
            or lower in SECRET_NAMES
            or path.suffix.lower() in SECRET_EXTENSIONS
            or "secret" in lower and path.suffix.lower() in {".json", ".yml", ".yaml", ".txt"}
        )

    def _safe_path(self, project: str, relative: str) -> Path:
        root = self._find_project(project).resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise CoreError("path_outside_project") from error
        if not candidate.is_file():
            raise CoreError("file_not_found")
        if self._secret_name(candidate):
            raise CoreError("secret_file_blocked")
        return candidate

    def read_file(self, project: str, relative: str, max_bytes: int = 32_000) -> dict[str, Any]:
        path = self._safe_path(project, relative)
        if path.stat().st_size > max_bytes:
            raise CoreError("file_too_large")
        data = path.read_text(encoding="utf-8", errors="replace")
        return {"project": project, "path": relative, "content": data[:max_bytes]}

    def _obsidian_root(self) -> Path | None:
        if not self.settings.obsidian_root:
            return None
        path = Path(self.settings.obsidian_root).expanduser()
        return path.resolve() if path.exists() and path.is_dir() else None

    def _search_roots(
        self, query: str, roots: list[tuple[str, Path]], limit: int
    ) -> dict[str, Any]:
        needle = query.strip().lower()
        if len(needle) < 2:
            raise CoreError("query_too_short")
        hits: list[dict[str, Any]] = []
        for label, root in roots:
            for path in self._iter_text_files(root, max_files=700):
                try:
                    if path.stat().st_size > 512_000:
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                pos = text.lower().find(needle)
                if pos < 0:
                    continue
                start = max(0, pos - 120)
                end = min(len(text), pos + len(needle) + 220)
                try:
                    rel = str(path.relative_to(root))
                except ValueError:
                    rel = path.name
                hits.append({
                    "project": label,
                    "path": rel,
                    "snippet": " ".join(text[start:end].split())[:360],
                })
                if len(hits) >= limit:
                    return {"query": query, "hits": hits}
        return {"query": query, "hits": hits}

    def search(self, query: str, project: str = "", limit: int = 30) -> dict[str, Any]:
        roots: list[tuple[str, Path]] = []
        if project:
            roots.append((project, self._find_project(project)))
        else:
            roots.extend((path.name, path) for path in self._project_paths(limit=40))
            obsidian = self._obsidian_root()
            if obsidian:
                roots.append(("Obsidian", obsidian))
        return self._search_roots(query, roots, limit)

    def _iter_text_files(self, root: Path, max_files: int):
        count = 0
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for filename in files:
                path = Path(base) / filename
                if self._secret_name(path) or path.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                yield path
                count += 1
                if count >= max_files:
                    return

    # ---------------------------------------------------------- safe actions
    def action(self, action: str, project: str = "") -> dict[str, Any]:
        allowed = {
            "git_status", "git_fetch", "git_pull_ff", "tests", "build", "open_folder"
        }
        if action not in allowed:
            raise CoreError("action_not_allowed")
        if private_mode.enabled() and action in ("git_fetch", "git_pull_ff"):
            raise CoreError("blocked_private_mode")
        path = self._find_project(project)
        if action == "git_status":
            result = {"action": action, "project": project, **self._git(path, "status", "--short", "--branch")}
            self._invalidate_project_cache()
            return result
        if action == "git_fetch":
            result = {"action": action, "project": project, **self._git(path, "fetch", "--prune", timeout=45)}
            self._invalidate_project_cache()
            return result
        if action == "git_pull_ff":
            result = {"action": action, "project": project, **self._git(path, "pull", "--ff-only", timeout=45)}
            self._invalidate_project_cache()
            return result
        if action == "tests":
            cmd = self._test_command(path)
            if not cmd:
                raise CoreError("no_known_test_command")
            return {"action": action, "project": project, **self._run(cmd, path, timeout=180)}
        if action == "build":
            cmd = self._build_command(path)
            if not cmd:
                raise CoreError("no_known_build_command")
            return {"action": action, "project": project, **self._run(cmd, path, timeout=300)}
        if os.name != "nt":
            raise CoreError("open_folder_windows_only")
        os.startfile(path)  # type: ignore[attr-defined]
        return {"ok": True, "action": action, "project": project, "output": str(path)}

    def _test_command(self, path: Path) -> list[str] | None:
        if (path / "tests").exists() and ((path / "pyproject.toml").exists() or (path / "requirements.txt").exists()):
            return [shutil.which("python") or "python", "-m", "pytest", "-q"]
        if (path / "package.json").exists() and shutil.which("npm"):
            return ["npm", "test"]
        if (path / "Cargo.toml").exists() and shutil.which("cargo"):
            return ["cargo", "test"]
        if (path / "go.mod").exists() and shutil.which("go"):
            return ["go", "test", "./..."]
        return None

    def _build_command(self, path: Path) -> list[str] | None:
        if (path / "platformio.ini").exists():
            if shutil.which("pio"):
                return ["pio", "run"]
            return [shutil.which("python") or "python", "-m", "platformio", "run"]
        if (path / "package.json").exists() and shutil.which("npm"):
            return ["npm", "run", "build"]
        if (path / "Cargo.toml").exists() and shutil.which("cargo"):
            return ["cargo", "build"]
        if (path / "go.mod").exists() and shutil.which("go"):
            return ["go", "build", "./..."]
        return None

    def _run(self, command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
        # Builds/tests may download dependencies or execute project-defined code.
        private_mode.guard_subprocess("project command", local=command in (
            ["git", "branch", "--show-current"], ["git", "status", "--porcelain"],
            ["git", "status", "--short", "--branch"], ["git", "log", "-1", "--pretty=%h %s"],
            ["git", "log", "-5", "--pretty=%h %ad %s", "--date=short"],
            ["git", "rev-parse", "--is-inside-work-tree"],
        ))
        if private_mode.enabled():
            # Even status can invoke an arbitrary repository fsmonitor hook.
            command = ["git", "--no-pager", "-c", "core.fsmonitor=false", *command[1:]]
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"ok": False, "returncode": -1, "output": str(error)[:12000]}
        output = ((completed.stdout or "") + (completed.stderr or ""))[-12000:]
        return {"ok": completed.returncode == 0, "returncode": completed.returncode, "output": output}

    # ----------------------------------------------------------- Cardputer LAN
    def _cardputer_headers(self) -> dict[str, str]:
        return {"X-MAZ-Token": self.settings.token}

    def note_device(self, ip: str) -> None:
        """Remember the device's LAN address when it calls in, so the live view
        works even where piblo.local (mDNS) does not resolve."""
        if ip and is_lan_ip(ip) and not ip.startswith("127."):
            self.device_ip = ip

    def _cardputer_bases(self) -> list[str]:
        bases = []
        if getattr(self, "device_ip", None):
            bases.append(f"http://{self.device_ip}")
        configured = self.settings.cardputer_url.rstrip("/")
        if configured not in bases:
            bases.append(configured)
        if "http://mazpocket.local" not in bases:  # devices still on pre-1.5.1 firmware
            bases.append("http://mazpocket.local")
        return bases

    def _cardputer(self, method: str, path: str, timeout: float, **kwargs: Any) -> httpx.Response:
        last: Exception | None = None
        for base in self._cardputer_bases():
            try:
                response = self.client.request(method, f"{base}{path}", headers=self._cardputer_headers(),
                                               timeout=timeout, **kwargs)
                return response
            except httpx.HTTPError as error:
                last = error
        raise CoreError(f"cardputer_unreachable:{last}")

    def cardputer_status(self) -> dict[str, Any]:
        try:
            response = self._cardputer("GET", "/api/status", timeout=3)
            response.raise_for_status()
            return response.json()
        except (CoreError, httpx.HTTPError, ValueError) as error:
            return {"ok": False, "error": f"cardputer_unreachable:{error}"}

    def cardputer_screen(self) -> bytes:
        response = self._cardputer("GET", "/api/screen", timeout=4)
        if response.status_code != 200:
            raise CoreError(f"cardputer_http_{response.status_code}")
        if len(response.content) != 240 * 135 * 2:
            raise CoreError("invalid_cardputer_frame")
        return response.content

    def cardputer_action(self, action: str) -> dict[str, Any]:
        """Forward one portal action (update, key:..., open:...). Returns the device's own reply."""
        response = self._cardputer("POST", "/api/action", timeout=4, data={"action": action})
        return {"ok": response.status_code < 300, "status": response.status_code, "text": response.text[:300]}

    def cardputer_key(self, keys: str) -> dict[str, Any]:
        response = self._cardputer("POST", "/api/key", timeout=4, data={"keys": keys})
        if response.status_code != 200:
            raise CoreError(f"cardputer_http_{response.status_code}")
        return response.json()

    def cardputer_config(self, volume: int | None = None, brightness: int | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if volume is not None:
            data["volume"] = str(volume)
        if brightness is not None:
            data["brightness"] = str(brightness)
        if not data:
            raise CoreError("no_config_fields")
        response = self._cardputer("POST", "/api/config", timeout=4, data=data)
        if response.status_code != 200:
            raise CoreError(f"cardputer_http_{response.status_code}")
        return response.json()

    @staticmethod
    def rgb565_to_png(frame: bytes, width: int = 240, height: int = 135) -> bytes:
        if len(frame) != width * height * 2:
            raise CoreError("invalid_cardputer_frame")
        # Expand RGB565 little-endian pairs to 8-bit RGB scanlines (no Pillow),
        # matching the web view's conversion (R=v>>11, G=v>>5, B=v&31).
        pixels = bytearray(width * height * 3)
        index = 0
        for i in range(0, len(frame), 2):
            value = frame[i] | (frame[i + 1] << 8)
            r5 = (value >> 11) & 0x1F
            g6 = (value >> 5) & 0x3F
            b5 = value & 0x1F
            pixels[index] = (r5 << 3) | (r5 >> 2)        # *255/31
            pixels[index + 1] = (g6 << 2) | (g6 >> 4)    # *255/63
            pixels[index + 2] = (b5 << 3) | (b5 >> 2)    # *255/31
            index += 3
        raw = b"".join(b"\x00" + bytes(pixels[y * width * 3:(y + 1) * width * 3])
                       for y in range(height))

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw, 9))
                + chunk(b"IEND", b""))

    def cardputer_png(self) -> bytes:
        return self.rgb565_to_png(self.cardputer_screen())

    # --------------------------------------------------------------- grounding
    def context_for_prompt(self, text: str) -> str:
        if not self.settings.core_enabled:
            return ""
        lower = text.lower()
        paths = self._project_paths(limit=60)
        matched_paths = [path for path in paths if path.name.lower() in lower]

        # No Git fan-out for generic chat. Only projects explicitly named by
        # the user get deep branch/status/recent-commit evidence.
        evidence: dict[str, Any] = {}
        # ~300 prompt tokens that LFM3 re-reads on every turn (no prompt cache): only send the PC/project
        # overview when the question is about the machine or code, not for generic chat.
        if matched_paths or _PC_WORDS.search(lower):
            evidence["pc"] = {
                "hostname": socket.gethostname(),
                "ollama_model": self._selected_local_model(),
            }
            evidence["known_projects"] = [
                {"name": path.name, "kind": self._project_kind(path)} for path in paths[:20]
            ]
        if matched_paths:
            detailed = []
            for path in matched_paths[:2]:
                try:
                    detailed.append(self.project(path.name))
                except CoreError:
                    pass
            evidence["matched_project_evidence"] = detailed

        if any(word in lower for word in ("cardputer", "pocket", "device", "wifi")):
            evidence["cardputer"] = self.cardputer_status()

        if any(word in lower for word in ("note", "memory", "obsidian", "remember")):
            terms = [
                word for word in re.findall(r"[a-zA-Z0-9_-]{4,}", text)
                if word.lower() not in {"what", "with", "from", "that", "this", "remember"}
            ]
            if terms:
                try:
                    if matched_paths:
                        evidence["memory_hits"] = self.search(
                            terms[-1], matched_paths[0].name, limit=6
                        )["hits"]
                    else:
                        obsidian = self._obsidian_root()
                        if obsidian:
                            evidence["memory_hits"] = self._search_roots(
                                terms[-1], [("Obsidian", obsidian)], 6
                            )["hits"]
                except CoreError:
                    pass

        encoded = json.dumps(evidence, ensure_ascii=False, default=str)
        return "\nMAZ Core factual evidence (use this, do not invent beyond it):\n" + encoded[:7000]

    # ------------------------------------------------------------- bridge API
    def dispatch(self, command: dict[str, Any]) -> dict[str, Any]:
        kind = str(command.get("command", "")).strip()
        if kind == "status":
            return self.status()
        if kind == "projects":
            return {"ok": True, "projects": self.projects()}
        if kind == "project":
            return {"ok": True, "project": self.project(str(command.get("project", "")))}
        if kind == "search":
            return self.search(str(command.get("query", "")), str(command.get("project", "")))
        if kind == "read":
            return {"ok": True, **self.read_file(str(command.get("project", "")), str(command.get("path", "")))}
        if kind == "run":
            return self.action(str(command.get("action", "")), str(command.get("project", "")))
        if kind == "cardputer":
            return self.cardputer_status()
        raise CoreError("command_not_allowed")
