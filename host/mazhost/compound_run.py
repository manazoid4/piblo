"""Run a two-step voice request ("save the spreadsheet and remind me to finish it tonight").

Steps come from compound.parse_compound. Anything that changes the PC waits for one YES on Piblo
(the existing Buddy approval queue); reminders, promises and focus just happen. Steps run in order
and stop at the first failure, so a reminder is never set for a file that did not save."""
from __future__ import annotations

import ctypes
import re
import sys
import threading
import time

from .buddy import AlertIn
from .compound import describe, needs_approval
from .promises import PromiseError, propose

VK_CONTROL, VK_S = 0x11, 0x53
APPROVAL_WAIT_S = 90


def doc_name(title: str) -> str:
    """'Budget.xlsx - Excel' -> 'Budget.xlsx'. Window titles are untrusted text: clip them."""
    name = re.split(r"\s+[-–—|]\s+", title or "")[0].strip()
    return re.sub(r"[\x00-\x1f]", "", name)[:40] or "your file"


def _stem(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name)


def foreground() -> dict:
    from .flow_routes import foreground_window   # Windows only
    return foreground_window()


def ctrl_s(hwnd: int, window=foreground) -> None:
    """Press Ctrl+S, but only into the window the user was in when they asked."""
    if sys.platform != "win32":
        raise RuntimeError("save needs Windows")
    u = ctypes.windll.user32
    if window()["hwnd"] != hwnd:
        u.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        if window()["hwnd"] != hwnd:
            raise RuntimeError("window changed; not saved")
    for vk in (VK_CONTROL, VK_S):
        u.keybd_event(vk, 0, 0, 0)
    for vk in (VK_S, VK_CONTROL):
        u.keybd_event(vk, 0, 2, 0)


class CompoundRunner:
    def __init__(self, buddy, promises, focus, pc, window=foreground, save=ctrl_s):
        self.buddy, self.promises, self.focus, self.pc = buddy, promises, focus, pc
        self.window, self.save = window, save
        self.last: dict = {}

    def start(self, text: str, steps: list[dict]) -> dict:
        win = self.window() if any(s["type"] == "pc.save" for s in steps) else {"hwnd": 0, "title": ""}
        subject = doc_name(win["title"]) if win["title"] else ""
        labels = [describe(s, subject) for s in steps]
        run = {"text": text, "steps": labels, "subject": subject, "asked_at": time.time(),
               "approval": None, "results": [], "state": "running"}
        self.last = run
        if not needs_approval(steps):
            self._execute(run, steps, win)
            return {"reply": "Done: " + "; ".join(r["label"] for r in run["results"]), "run": run}
        approval = self.buddy.request("Voice: two steps", " + ".join(labels), category="other",
                                      project="voice", undo="")
        run["approval"], run["state"] = approval["id"], "waiting_for_yes"
        threading.Thread(target=self._after_yes, args=(run, steps, win), daemon=True).start()
        return {"reply": "Press YES on Piblo to " + " and ".join(l.lower() for l in labels) + ".", "run": run}

    def _after_yes(self, run: dict, steps: list[dict], win: dict) -> None:
        decision = self.buddy.state(run["approval"], wait=APPROVAL_WAIT_S)
        run["decision"] = decision.get("decision")
        if run["decision"] != "allow":
            run["state"] = "declined" if run["decision"] else "expired"
            return
        self._execute(run, steps, win)
        ok = run["state"] == "done"
        self.buddy.post_alert(AlertIn(source="voice", kind="info",
                                      title=("Done: " if ok else "Stopped: ") + run["steps"][0][:50],
                                      body="; ".join(r["label"] for r in run["results"])[:200]))

    def _execute(self, run: dict, steps: list[dict], win: dict) -> None:
        for step in steps:
            try:
                label = self._one(step, run["subject"], win)
                run["results"].append({"ok": True, "label": label})
            except (RuntimeError, PromiseError, OSError) as error:
                run["results"].append({"ok": False, "label": f"{describe(step, run['subject'])}: {error}"})
                run["state"] = "failed"
                return
        run["state"], run["done_at"] = "done", time.time()

    def _one(self, step: dict, subject: str, win: dict) -> str:
        kind = step["type"]
        if kind == "pc.save":
            self.save(win["hwnd"])
            return f"Saved {subject or 'the file'}"
        if kind == "promise.create":
            task = step["task"]
            if step.get("needs_subject") and subject:
                task = re.sub(r"\bit\b", lambda _m: _stem(subject), task, flags=re.I)
            note = f"{task} {step['when']}"
            due = propose(note)["candidates"][0]
            saved = self.promises.confirm(note, due["due"])
            return f"Reminder {due['label']}: {task}"[:80] if saved["ok"] else "Reminder not saved"
        if kind == "focus.start":
            self.focus.start(step["minutes"])
            return f"Focus {step['minutes']} min started"
        if kind == "reminder.create":
            self.buddy.post_alert(AlertIn(source="reminder", kind="reminder", title=step["title"][:60],
                                          priority="high", actions=["ack", "snooze"],
                                          delay_seconds=step["delay_seconds"]))
            return f"Reminder in {step['delay_seconds'] // 60} min: {step['title']}"
        if kind == "pc.action":
            return self.pc.perform(step["action"]).label
        raise RuntimeError(f"unsupported step {kind}")
