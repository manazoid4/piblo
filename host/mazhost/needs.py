"""Needs-you queue: everything waiting on the owner, one list, three fixed tiers."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from . import private_mode
from .nowindow import no_window

TIERS = ("approvals", "prs", "actions")
PR_CACHE_S = 60


def _hid(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:10]


class Needs:
    def __init__(self, buddy, data_dir: Path) -> None:
        self.buddy = buddy
        self.state_file = Path(data_dir) / "needs_state.json"
        self._lock = threading.Lock()
        self._pr_cache: dict[str, tuple[float, list[dict]]] = {}

    # ---- done / snooze state (small JSON file) ----
    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def mark(self, item_id: str, *, done: bool = False, hours: float = 0) -> None:
        with self._lock:
            state = self._load()
            state[item_id] = {"done": True} if done else {"until": time.time() + hours * 3600}
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            tmp.replace(self.state_file)

    def _hidden(self, state: dict, item_id: str) -> bool:
        s = state.get(item_id)
        return bool(s) and (s.get("done") or s.get("until", 0) > time.time())

    # ---- tier 1: approvals ----
    def _approvals(self) -> list[dict]:
        now = time.time()
        return [{"id": "a-" + i["id"], "tier": "approvals",
                 "title": f"Approve {i['tool']}", "project": i.get("project", ""),
                 "age": int(now - i["t"]), "detail": i.get("summary", "")}
                for i in self.buddy.pending()]

    # ---- tier 2: UNPROVEN PRs ----
    def _repo_prs(self, repo: str) -> list[dict]:
        hit = self._pr_cache.get(repo)
        if hit and time.monotonic() - hit[0] < PR_CACHE_S:
            return hit[1]
        private_mode.guard_subprocess("GitHub PR list")
        out = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "100",
             "--json", "number,title,createdAt,url"],
            capture_output=True, text=True, timeout=20, check=True, **no_window()).stdout
        prs = json.loads(out)
        self._pr_cache[repo] = (time.monotonic(), prs)  # failures are never cached
        return prs

    def _prs(self, warnings: list[str]) -> list[dict]:
        from datetime import datetime
        if private_mode.enabled():
            return []  # `gh` reaches GitHub: skipped cleanly in private mode
        items = []
        now = time.time()
        for repo in (r.strip() for r in os.environ.get("MAZ_NEEDS_REPOS", "").split(",")):
            if not repo:
                continue
            try:
                prs = self._repo_prs(repo)
            except Exception as e:  # noqa: BLE001 - gh missing, not logged in, offline, bad JSON
                warnings.append(f"prs skipped for {repo}: {type(e).__name__}")
                continue
            for p in prs:
                if "UNPROVEN" not in p.get("title", ""):
                    continue
                try:
                    created = datetime.fromisoformat(p["createdAt"].replace("Z", "+00:00")).timestamp()
                except (KeyError, ValueError):
                    created = now
                items.append({"id": _hid("pr", repo, str(p["number"])), "tier": "prs",
                              "title": p["title"], "project": repo.split("/")[-1],
                              "age": int(now - created), "detail": p.get("url", "")})
        return items

    # ---- tier 3: owner-action lines in handoff markdown ----
    def _actions(self, warnings: list[str]) -> list[dict]:
        root = os.environ.get("MAZ_HANDOFF_DIR", "").strip()
        if not root:
            return []
        items = []
        now = time.time()
        try:
            files = sorted(Path(root).expanduser().rglob("*.md"))
        except OSError as e:
            warnings.append(f"actions skipped: {type(e).__name__}")
            return []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
                age = int(now - f.stat().st_mtime)
            except OSError:
                continue
            owner_section = False
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("#"):
                    owner_section = "owner" in s.lower()
                    continue
                if s.startswith("Maz:"):
                    title = s[4:].strip()
                elif owner_section and s.startswith("- [ ]"):
                    title = s[5:].strip()
                else:
                    continue
                if title:
                    items.append({"id": _hid("act", f.name, title), "tier": "actions", "title": title,
                                  "project": f.stem, "age": age, "detail": f.name})
        return items

    def queue(self) -> dict:
        warnings: list[str] = []
        state = self._load()
        tiers = {
            "approvals": self._approvals(),
            "prs": [i for i in self._prs(warnings) if not self._hidden(state, i["id"])],
            "actions": [i for i in self._actions(warnings) if not self._hidden(state, i["id"])],
        }
        items = []
        for t in TIERS:
            tiers[t].sort(key=lambda i: -i["age"])  # oldest first
            items += tiers[t]
        out = {"count": len(items), "tiers": {t: len(tiers[t]) for t in TIERS}, "items": items}
        if warnings:
            out["warnings"] = warnings
        return out
