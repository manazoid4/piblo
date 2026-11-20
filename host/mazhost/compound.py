"""Two-step voice requests ("save the budget and remind me to finish it tonight").

Pure parsing: nothing here saves, schedules or touches the PC. The caller runs each step through its own
path and asks for approval first when needs_approval() says so."""
from __future__ import annotations

import re

from .commands import parse_command

SEPARATORS = [", and then ", " and then ", ", then ", " then ", " and "]
LEAD = re.compile(r"^\s*(?:(?:hey\s+)?piblo\b[,.]?\s*|please\b\s*)+", re.I)
TIME = r"(?:\d{1,2}(?::[0-5]\d)?\s?(?:am|pm)|\d{1,2}:[0-5]\d)"
DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
WHEN = (rf"(?:(?:tonight|today|tomorrow)\s+at\s+{TIME}|tonight\s+at\s+\d{{1,2}}\b|at\s+{TIME}"
        rf"|tonight|today|tomorrow|this evening|(?:on\s+)?(?:{DAYS}))")
PROMISE_A = re.compile(rf"^remind me to (?P<task>.+?) (?P<when>{WHEN})$", re.I)
PROMISE_B = re.compile(rf"^remind me (?P<when>{WHEN}) to (?P<task>.+)$", re.I)
SAVE = re.compile(
    r"^save (?:(?:the|this|my|that)\s+)?(?P<noun>spreadsheet|sheet|workbook|document|doc|file|it|this|work)"
    r"(?:\s+(?:i['’]?m\s+|i\s+am\s+)?(?:working\s+)?on)?$",
    re.I,
)
SAVE_WHAT = {"spreadsheet": "spreadsheet", "sheet": "sheet", "workbook": "workbook", "document": "document",
             "doc": "document"}
FOCUS = re.compile(
    r"^(?:start\s+)?(?:an?\s+)?(?:focus(?:\s+session)?\s+for\s+(?P<a>\d{1,3})\s+minutes?"
    r"|(?P<b>\d{1,3})\s+minute\s+focus(?:\s+session)?)$",
    re.I,
)
ACTION_LABELS = {"mute": "Mute PC", "lock": "Lock PC", "desktop": "Show desktop", "play_pause": "Play/pause",
                 "volume_up": "Volume up", "volume_down": "Volume down", "previous_track": "Previous track",
                 "next_track": "Next track"}


def _clean(part: str) -> str:
    return LEAD.sub("", part.strip()).strip().rstrip(".!?,;").strip()


def _when(raw: str) -> str:
    phrase = " ".join(raw.lower().split())
    if phrase == "this evening":
        return "tonight"
    if phrase in DAYS.split("|"):
        return f"on {phrase}"
    if phrase.startswith("at "):   # a bare time means today; propose() needs a day word
        return f"today {phrase}"
    return phrase


def parse_step(part: str) -> dict | None:
    if step := parse_command(part):
        return step
    if m := SAVE.match(part):
        return {"type": "pc.save", "what": SAVE_WHAT.get(m["noun"].lower(), "file")}
    if m := (PROMISE_A.match(part) or PROMISE_B.match(part)):
        task, when = m["task"].strip(), _when(m["when"])
        return {"type": "promise.create", "task": task, "when": when, "note": f"{task} {when}",
                "needs_subject": bool(re.search(r"\bit\b", task, re.I))}
    if m := FOCUS.match(part):
        minutes = int(m["a"] or m["b"])
        if 1 <= minutes <= 180:
            return {"type": "focus.start", "minutes": minutes}
    return None


def _split_points(text: str) -> list[tuple[int, str]]:
    points = [(m.start(), sep) for sep in SEPARATORS for m in re.finditer(re.escape(sep), text, re.I)]
    return sorted(points, key=lambda p: (p[0], -len(p[1])))   # left to right, longer separator first


def parse_compound(text: str) -> list[dict] | None:
    """Exactly two steps split at one separator, or None. Each half must parse on its own."""
    for at, sep in _split_points(text):
        left, right = _clean(text[:at]), _clean(text[at + len(sep):])
        if not left or not right:
            continue
        first, second = parse_step(left), parse_step(right)
        if first and second:
            return [first, second]
    return None


def needs_approval(steps: list[dict]) -> bool:
    """Saving and locking change state on the PC; reminders, promises and focus do not."""
    return any(s["type"] == "pc.save" or (s["type"] == "pc.action" and s["action"] == "lock") for s in steps)


def _fit(text: str, limit: int = 28) -> str:
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def describe(step: dict, subject: str = "") -> str:
    """Short device-screen label (<= 28 characters)."""
    kind = step["type"]
    if kind == "pc.save":
        return _fit(f"Save {subject or step['what']}")
    if kind == "promise.create":
        task = step["task"]
        if subject and step["needs_subject"]:
            task = re.sub(r"\bit\b", lambda _m: re.sub(r"\.\w{2,5}$", "", subject), task, flags=re.I)
        full = f"Remind {step['when']}: {task}"
        return full if len(full) <= 28 else _fit(f"{step['when'].capitalize()}: {task}")
    if kind == "focus.start":
        return f"Focus {step['minutes']} min"
    if kind == "reminder.create":
        return _fit(f"Remind in {step['delay_seconds'] // 60} min: {step['title']}")
    if kind == "pc.action":
        return ACTION_LABELS.get(step["action"], step["action"].replace("_", " ").capitalize())
    return _fit(kind)
