"""Promise Keeper: "I said I'd sort it, Piblo makes sure I do."

A thin layer over the existing Buddy alert store and scheduler (no second store, no second scheduler):
a promise is a kind="deadline" alert with source="promise". Dates are parsed deterministically and are
only ever proposed; nothing is saved until the user confirms an exact timestamp."""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from hashlib import sha1
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .buddy import MAX_DELAY_S, AlertIn, Buddy, SnoozeBody

SOURCE = "promise"
DEFAULT_HOUR = 9
CARD_TITLE, CARD_WHEN = 28, 16      # 240x135 device: one short line each
SEP = " | "
_MONTHS = ("january february march april may june july august september october november december").split()
_MON = "|".join(sorted({m for n in _MONTHS for m in (n, n[:3])} | {"sept"}, key=len, reverse=True))
_DAYS = ("monday tuesday wednesday thursday friday saturday sunday").split()
_NUM = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})\b")
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MON})\b(?:,?\s+(\d{{4}}))?")
_MDY = re.compile(rf"\b({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?")
_DAY = re.compile(rf"\b(?:(next|this)\s+)?({'|'.join(_DAYS)})\b")
_IN = re.compile(r"\bin (\d{1,3}) (hour|day|week)s?\b")
_AMPM = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b")
_H24 = re.compile(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b")


class PromiseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("PIBLO_TZ", "Europe/London"))


def _mon(token: str) -> int:
    return next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(token[:3]))


def _date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _local(day: date, hh: int, mm: int, zone: ZoneInfo) -> datetime:
    """Aware local datetime; a wall time the clocks skip (spring forward) is rejected, never guessed."""
    dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone)
    if dt.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != dt.replace(tzinfo=None):
        raise PromiseError("nonexistent_time", "The clocks skip that time. Pick a time an hour either side.")
    return dt


def label(dt: datetime) -> str:
    return f"{dt:%a} {dt.day} {dt:%b %Y}, {dt:%H:%M} {dt.tzname()}"


def _time(text: str) -> tuple[int, int, bool]:
    if m := _AMPM.search(text):
        h = int(m[1])
        if not 1 <= h <= 12:
            raise PromiseError("invalid_time", "That time is not valid.")
        return h % 12 + (12 if m[3] == "pm" else 0), int(m[2] or 0), False
    if m := _H24.search(text):
        return int(m[1]), int(m[2]), False
    return DEFAULT_HOUR, 0, True


def _year(raw: str) -> int:
    y = int(raw)
    y = y + 2000 if len(raw) == 2 else y
    if not 2000 <= y <= 2100:
        raise PromiseError("invalid_year", "That year is not valid.")
    return y


def propose(note: str, now: datetime | None = None) -> dict:
    """Candidate due times for a note. Ambiguity returns every reading; the caller must pick one."""
    zone = tz()
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    text, today = note.lower(), now.date()
    days: list[date] = []
    exact: datetime | None = None
    today_word = False
    if m := _NUM.search(text):
        y = _year(m[3])
        reads = [_date(y, int(m[2]), int(m[1])), _date(y, int(m[1]), int(m[2]))]   # British first, then US
        days = list(dict.fromkeys(d for d in reads if d))
        if not days:
            raise PromiseError("invalid_date", "That date does not exist.")
    elif m := _DMY.search(text) or _MDY.search(text):
        dmy = m.re is _DMY
        d, mon, raw = (int(m[1]), _mon(m[2]), m[3]) if dmy else (int(m[2]), _mon(m[1]), m[3])
        if raw:
            day = _date(_year(raw), mon, d)
        else:
            day = _date(today.year, mon, d)
            if day and day < today:
                day = _date(today.year + 1, mon, d)
        if not day:
            raise PromiseError("invalid_date", "That date does not exist.")
        days = [day]
    elif m := _IN.search(text):
        n = int(m[1])
        if m[2] == "hour":   # elapsed time, so add it in UTC and let the clock change fall where it falls
            exact = (now.astimezone(timezone.utc) + timedelta(hours=n)).astimezone(zone).replace(second=0, microsecond=0)
        else:                # calendar days: same wall-clock time, honouring a stated time
            days = [today + timedelta(days=n * (7 if m[2] == "week" else 1))]
    elif "tomorrow" in text:
        days = [today + timedelta(days=1)]
    elif "today" in text:
        days, today_word = [today], True
    elif m := _DAY.search(text):
        coming = (_DAYS.index(m[2]) - today.weekday()) % 7 or 7
        days = [today + timedelta(days=coming)]
        # "next Friday" is genuinely ambiguous while this week's Friday is still ahead: offer both.
        if m[1] == "next" and today.weekday() + coming <= 6:
            days.append(days[0] + timedelta(days=7))
    else:
        raise PromiseError("no_date", "I could not find a date. Say when, for example 'by 3 April' or 'Friday at 5pm'.")
    hh, mm, default_time = _time(text)
    if exact:
        cands, default_time = [exact], False
    else:
        cands = [_local(d, hh, mm, zone) for d in days]
        if today_word and default_time and cands[0] <= now:
            # No time said and 09:00 has gone: end of the working day, or an hour from now if that has gone too.
            late = _local(today, 17, 0, zone)
            cands = [late if late > now else (now.astimezone(timezone.utc) + timedelta(hours=1))
                     .astimezone(zone).replace(second=0, microsecond=0)]
    if cands[0] <= now:   # the first (British) reading is past: say so, never fall through to the US one
        raise PromiseError("in_the_past", "That time has already passed.")
    out = [{"due": c.replace(tzinfo=None).isoformat(timespec="minutes"), "label": label(c)}
           for c in cands if c > now]
    if not out:
        raise PromiseError("in_the_past", "That time has already passed.")
    return {"ok": True, "note": note, "candidates": out, "ambiguous": len(out) > 1,
            "default_time": default_time, "confirm_required": True}


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 3] + "..."


class Promises:
    def __init__(self, buddy: Buddy):
        self.buddy = buddy

    def confirm(self, note: str, due: str) -> dict:
        zone = tz()
        try:
            naive = datetime.fromisoformat(due)
        except ValueError:
            raise PromiseError("invalid_date", "due must be a local date and time, like 2027-04-03T09:00.")
        dt = _local(naive.date(), naive.hour, naive.minute, zone) if naive.tzinfo is None else naive.astimezone(zone)
        delay = int(dt.timestamp() - time.time())
        if delay <= 0:
            raise PromiseError("in_the_past", "That time has already passed.")
        if delay > MAX_DELAY_S:
            # shortcut: the alert scheduler holds 30 days; upgrade with an optional due_at on AlertIn.
            raise PromiseError("too_far", "Piblo can hold a promise up to 30 days ahead for now.")
        key = "pk-" + sha1(f"{note.strip()}|{dt.timestamp():.0f}".encode()).hexdigest()[:20]
        when = label(dt)
        res = self.buddy.post_alert(AlertIn(
            source=SOURCE, kind="deadline", title=_clip(note, 60), body=(when + SEP + note.strip())[:200],
            actions=["ack", "snooze"], delay_seconds=delay, idempotency_key=key))
        return {"ok": True, "id": res["id"], "due_label": when, "due_epoch": dt.timestamp(),
                "duplicate": bool(res.get("duplicate"))}

    def _all(self) -> list[dict]:
        return [r for r in self.buddy.alerts()["alerts"] if r["source"] == SOURCE]

    def _check(self, pid: str) -> None:
        if not any(r["id"] == pid for r in self._all()):
            raise HTTPException(404, "promise_not_found")

    def _card(self, rec: dict, state: str) -> dict:
        due = datetime.fromtimestamp(rec["due"], tz())
        when, _, note = rec["body"].partition(SEP)
        return {"id": rec["id"], "state": state, "title": _clip(note, CARD_TITLE), "note": note,
                "when": f"{due:%a} {due.day} {due:%b %H:%M}"[:CARD_WHEN], "due_label": when,
                "overdue": state == "due" and rec["due"] < time.time(),
                "actions": ["undo"] if state == "done" else ["done", "later"]}

    def today(self) -> dict:
        """Up to three actionable cards (due, waiting, done) plus counts; empty groups are absent."""
        due, waiting, done = [], [], []
        for r in self._all():
            if r["outcome"] == "acked":
                done.append(r)
            elif r["fired"] and r["outcome"] is None:
                due.append(r)
            else:
                waiting.append(r)   # scheduled, or snoozed ("later")
        due.sort(key=lambda r: r["due"])
        waiting.sort(key=lambda r: r["snooze_until"] or r["due"])
        done.sort(key=lambda r: r.get("outcome_at") or 0, reverse=True)
        cards = [self._card(g[0], s) for g, s in ((due, "due"), (waiting, "waiting"), (done, "done")) if g]
        return {"ok": True, "cards": cards, "empty": not cards,
                "counts": {"due": len(due), "waiting": len(waiting), "done": len(done)}}

    def done(self, pid: str) -> dict:
        self._check(pid)
        return self.buddy.alert_outcome(pid, "ack")

    def later(self, pid: str, minutes: int) -> dict:
        self._check(pid)
        return self.buddy.alert_outcome(pid, "snooze", minutes)

    def undo(self, pid: str) -> dict:
        self._check(pid)
        try:
            return self.buddy.alert_undo(pid)
        except ValueError as error:
            raise HTTPException(400, str(error))


class Propose(BaseModel):
    note: str = Field(min_length=1, max_length=140)


class Confirm(Propose):
    due: str = Field(min_length=16, max_length=40)
    confirmed: bool    # the exact timestamp was shown to the user and they accepted it


def build_promises_router(service: Promises) -> APIRouter:
    router = APIRouter(prefix="/promises", tags=["promises"])

    def run(fn, *a):
        try:
            return fn(*a)
        except PromiseError as error:
            raise HTTPException(422, {"error": error.code, "message": error.message})
        except OverflowError as error:
            raise HTTPException(429, str(error))

    @router.post("/propose")
    def propose_route(body: Propose):
        return run(propose, body.note)

    @router.post("/confirm")
    def confirm(body: Confirm):
        if not body.confirmed:
            raise HTTPException(422, {"error": "not_confirmed", "message": "Confirm the exact date and time first."})
        return run(service.confirm, body.note, body.due)

    @router.get("/today")
    def today():
        return service.today()

    @router.post("/{pid}/done")
    def done(pid: str):
        return service.done(pid)

    @router.post("/{pid}/later")
    def later(pid: str, body: SnoozeBody | None = None):
        return service.later(pid, (body or SnoozeBody()).minutes)

    @router.post("/{pid}/undo")
    def undo(pid: str):
        return service.undo(pid)

    return router
