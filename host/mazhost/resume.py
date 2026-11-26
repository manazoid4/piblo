""""Piblo, carry on where I left off."

Builds one resume packet from what Piblo already knows, newest first:

* agent progress: tasks agents reported to /buddy/task (PibloCraft, Claude Code
  hooks, Hermes), preferring unfinished ones;
* what the user parked or was focusing on (/where: park notes, focus sprint,
  saved links, the last edited project document);
* browser context: the last pages from Chrome/Edge history, read locally from
  a copy of the History file, only when the user has granted `browser_history`.

`resume.continue` then acts on it: re-opens the document and page on the PC and
publishes `craft.resume.requested` with the task and its context on the event
bus, so PibloCraft (or any agent following events) picks the task back up
without the user repeating the instructions. It never resends a prompt to a
cloud model by itself.
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

UNFINISHED = ("running", "waiting", "failed", "blocked", "paused")
RECENT_S = 3 * 86_400
CHROMIUM_EPOCH_OFFSET_S = 11_644_473_600  # 1601-01-01 -> 1970-01-01


def chromium_history_paths() -> list[Path]:
    if platform.system() == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        roots = [local / "Google/Chrome/User Data", local / "Microsoft/Edge/User Data",
                 local / "BraveSoftware/Brave-Browser/User Data"]
    elif platform.system() == "Darwin":
        sup = Path.home() / "Library/Application Support"
        roots = [sup / "Google/Chrome", sup / "Microsoft Edge"]
    else:
        roots = [Path.home() / ".config/google-chrome", Path.home() / ".config/chromium",
                 Path.home() / ".config/microsoft-edge"]
    return [root / "Default/History" for root in roots if (root / "Default/History").exists()]


def recent_pages(paths: list[Path] | None = None, limit: int = 5, now: float | None = None) -> list[dict]:
    """Last visited pages across Chromium browsers. Copies the DB (the browser locks it)."""
    now = time.time() if now is None else now
    rows: list[dict] = []
    for path in (chromium_history_paths() if paths is None else paths):
        tmp = Path(tempfile.mkdtemp(prefix="piblo-hist-")) / "History"
        try:
            shutil.copy2(path, tmp)
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            try:
                for url, title, last in con.execute(
                        "SELECT url, title, last_visit_time FROM urls ORDER BY last_visit_time DESC LIMIT ?",
                        (limit * 3,)):
                    at = last / 1_000_000 - CHROMIUM_EPOCH_OFFSET_S
                    if not str(url).startswith(("http://", "https://")) or now - at > RECENT_S:
                        continue
                    rows.append({"url": url, "title": title or url, "at": at, "browser": path.parent.parent.name})
            finally:
                con.close()
        except (OSError, sqlite3.Error):
            continue
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)
    rows.sort(key=lambda r: -r["at"])
    seen, out = set(), []
    for r in rows:
        if r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out[:limit]


def _ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


class Resumer:
    def __init__(self, *, tasks: Callable[[], list[dict]], where: Callable[[], list[dict]],
                 pages: Callable[[], list[dict]] | None = None,
                 browser_allowed: Callable[[], bool] = lambda: False,
                 open_url: Callable[[str], Any] = webbrowser.open,
                 open_path: Callable[[str], Any] | None = None,
                 project_roots: list[Path] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.tasks, self.where, self.pages = tasks, where, pages or recent_pages
        self.browser_allowed = browser_allowed
        self.open_url = open_url
        self.open_path = open_path or _open_path
        self.project_roots = project_roots or []
        self.clock = clock

    def packet(self) -> dict:
        now = self.clock()
        tasks = []
        for t in self.tasks():
            updated = _ts(t.get("updated"))
            if now - updated <= RECENT_S:
                tasks.append({**t, "ago_s": round(now - updated)})
        unfinished = [t for t in tasks if t.get("status") in UNFINISHED]
        task = (unfinished or tasks or [None])[0]
        where = [w for w in self.where() if now - _ts(w.get("when")) <= RECENT_S]
        doc = next((w for w in where if w.get("kind") == "document"), None)
        parked = next((w for w in where if w.get("kind") in ("park", "focus")), None)
        link = next((w for w in where if w.get("kind") == "library" and str(w.get("source", "")).startswith("http")),
                    None)
        pages: list[dict] = []
        browser = "not_allowed"
        if self.browser_allowed():
            pages = self.pages()
            browser = "ok" if pages else "none_found"
        packet = {"task": task, "other_tasks": [t for t in unfinished if t is not task][:3],
                  "document": doc, "parked": parked, "link": link, "pages": pages[:3],
                  "browser_history": browser}
        packet["reply"] = self._say(packet)
        packet["empty"] = not any((task, doc, parked, link, pages))
        if not packet["empty"]:
            packet["next"] = {"capability": "resume.continue", "args": {}, "label": "Pick it back up"}
        return packet

    @staticmethod
    def _say(p: dict) -> str:
        bits = []
        t = p["task"]
        if t:
            who = f"{t['agent']} was " if t.get("agent") else "You were "
            state = {"failed": "stuck on", "waiting": "waiting on", "blocked": "blocked on"}.get(t["status"], "working on")
            if t.get("status") == "done":
                bits.append(f"Last finished: {t['title']}.")
            else:
                prog = f" ({t['progress']}%)" if t.get("progress") is not None else ""
                bits.append(f"{who}{state} {t['title']}{prog}.")
                if t.get("detail"):
                    bits.append(str(t["detail"])[:120].rstrip(".") + ".")
        if p["parked"]:
            bits.append(f"You parked: {p['parked']['title']}.")
        if p["document"]:
            bits.append(f"Last file: {p['document']['title']}.")
        if p["pages"]:
            bits.append(f"Last page: {p['pages'][0]['title'][:60]}.")
        if not bits:
            return "I can't find anything recent to pick up. Tell me what you were doing and I'll remember it."
        return " ".join(bits) + " Say 'carry on' and I'll reopen it and hand the task back to your agent."

    def continue_(self, packet: dict | None = None, open_on_pc: bool = True) -> dict:
        packet = packet or self.packet()
        opened = []
        if open_on_pc:
            doc = packet.get("document")
            if doc:
                path = self._doc_path(doc)
                if path and self.open_path(str(path)):
                    opened.append(str(path))
            page = (packet.get("pages") or [None])[0] or packet.get("link")
            if page:
                url = page.get("url") or page.get("source")
                if url and self.open_url(url):
                    opened.append(url)
        handoff = None
        if packet.get("task") and packet["task"].get("status") != "done":
            t = packet["task"]
            handoff = {"task_id": t["id"], "title": t["title"], "agent": t.get("agent", ""),
                       "detail": t.get("detail", ""), "status": t["status"],
                       "context": {"document": (packet.get("document") or {}).get("source"),
                                   "parked": (packet.get("parked") or {}).get("title"),
                                   "pages": [pg.get("url") for pg in packet.get("pages", [])]}}
        reply = []
        if opened:
            reply.append("Reopened " + ", ".join(Path(o).name if "://" not in o else "the page" for o in opened) + ".")
        if handoff:
            reply.append(f"Asked {handoff['agent'] or 'PibloCraft'} to carry on with {handoff['title']}.")
        return {"opened": opened, "handoff": handoff,
                "reply": " ".join(reply) or "Nothing to reopen.", "packet": packet}

    def _doc_path(self, doc: dict) -> Path | None:
        rel = str(doc.get("source", "")).removeprefix("project:")
        for root in self.project_roots:
            candidate = (Path(root) / rel).resolve()
            try:
                candidate.relative_to(Path(root).resolve())
            except ValueError:
                continue
            if candidate.exists():
                return candidate
        return None


def _open_path(path: str) -> bool:
    try:
        if platform.system() == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        return bool(webbrowser.open(Path(path).as_uri()))
    except OSError:
        return False


def install_resume(registry, *, resumer: Resumer) -> Resumer:
    from .universal_registry import Capability, Event

    async def _continue(args, context):
        result = resumer.continue_(open_on_pc=bool(args.get("open_on_pc", True)))
        if result["handoff"] and registry.event_bus:
            await registry.event_bus.publish(Event(type="craft.resume.requested", source="resume",
                                                   payload=result["handoff"]))
        return result

    registry.register(Capability(
        id="resume.packet", name="Where was I?", category="continuity", risk="read",
        description="Recover the last task, agent progress, file and page the user was on.",
        input_schema={"type": "object", "properties": {}}, output_schema={"type": "object"},
        handler=lambda args, context: resumer.packet()))
    registry.register(Capability(
        id="resume.continue", name="Carry on", category="continuity", risk="act", autonomous_ok=True,
        description="Reopen the last file and page and hand the unfinished task back to its agent.",
        input_schema={"type": "object", "properties": {"open_on_pc": {"type": "boolean"}}},
        output_schema={"type": "object"}, handler=_continue))
    return resumer
