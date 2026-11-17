"""Promise Keeper: "I said I'd sort it, Piblo makes sure I do."

A thin layer over the existing Buddy alert store and scheduler (no second store, no second scheduler):
a promise is a kind="deadline" alert with source="promise". Dates are parsed deterministically and are
only ever proposed; nothing is saved until the user confirms an exact timestamp.

Layered on top: bin-day and home reminders are recurring rules kept durable under the data dir
(promise_rules). One scheduler thread runs inside the host: at 7pm local time the Cardputer buzzes
"Tomorrow: ..." for anything due the next day, and Enter on the device marks it done and rolls the
rule forward. Nothing is saved until the user confirms on the Cardputer.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from .buddy import MAX_DELAY_S, AlertIn, Buddy, SnoozeBody
from .config import Settings
from .errors import RouteError
from .ledger import append_ledger

log = logging.getLogger("uvicorn.error")

SOURCE = "promise"
DEFAULT_HOUR = 9
CARD_TITLE, CARD_WHEN = 28, 16      # 240x135 device: one short line each
SEP = " | "
_MONTHS = ("january february march april may june july august september october november december").split()
_MON = "|".join(sorted({m for n in _MONTHS for m in (n, n[:3])} | {"sept"}, key=len, reverse=True))
_DAYS = ("monday tuesday wednesday thursday friday saturday sunday").split()
_NUM = re.compile(r"\b(\d{1,2})[/.\\-](\d{1,2})[/.\\-](\d{2}|\d{4})\b")
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MON})\b(?:,?\s+(\d{{4}}))?")
_MDY = re.compile(rf"\b({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?")
_DAY = re.compile(rf"\b(?:(next|this)\s+)?({'|'.join(_DAYS)})\b")
_IN = re.compile(r"\bin (\d{1,3}) (hour|day|week)s?\b")
_AMPM = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b")
_H24 = re.compile(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b")

# --- bin-day / home recurrence -----------------------------------------
WKDAY = {name: i for i, name in enumerate(_DAYS)} | {name[:3]: i for i, name in enumerate(_DAYS)}
WKNAME = {i: name[:3].title() for i, name in enumerate(_DAYS)}
_DAY_RE = re.compile(
    r"\b(" + "|".join(sorted({n for n in _DAYS} | {n[:3] for n in _DAYS}, key=len, reverse=True)) + r")\b"
)

# England & Wales bank holidays. Councils routinely move collections that fall
# on these, hence the optional per-bin +1 day shift.
BANK_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 4), date(2026, 5, 25),
    date(2026, 8, 31), date(2026, 12, 25), date(2026, 12, 26),
    date(2027, 1, 1), date(2027, 3, 26), date(2027, 3, 29), date(2027, 5, 3), date(2027, 5, 31),
    date(2027, 8, 30), date(2027, 12, 25), date(2027, 12, 26),
    date(2028, 1, 3), date(2028, 4, 14), date(2028, 4, 17), date(2028, 5, 1), date(2028, 5, 29),
    date(2028, 8, 28), date(2028, 12, 25), date(2028, 12, 26),
}


def _data_dir(settings: Settings) -> Path:
    if settings.data_dir:
        return Path(settings.data_dir).expanduser()
    return Path(__file__).resolve().parents[1] / "data"  # host/data in the install dir


def tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("PIBLO_TZ", "Europe/London"))


def _today_local() -> date:
    return datetime.now(tz()).date()


def _weekday_on_or_after(weekday: int, start: date) -> date:
    d = start
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def _shifted(d: date, shift: bool) -> date:
    """Council bank-holiday shift: push the collection a day at a time."""
    while shift and d in BANK_HOLIDAYS:
        d += timedelta(days=1)
    return d


def next_date(spec: dict, after: date | None = None, shift: bool | None = None) -> date:
    """Next occurrence of a recurrence rule. Strictly after `after` when given."""
    freq = spec.get("frequency", "weekly")
    interval = max(1, int(spec.get("interval_weeks", 1)))
    base = after if after is not None else _today_local()
    if freq == "fixed":
        best: date | None = None
        for iso in spec.get("fixed_dates") or []:
            _, m, dd = iso.split("-")
            cand = date(base.year, int(m), int(dd))
            while cand <= base:
                cand = date(cand.year + 1, int(m), int(dd))
            if best is None or cand < best:
                best = cand
        result = best
    else:
        weekday = int(spec["weekday"])
        anchor = date.fromisoformat(spec["anchor"])
        k = 0 if after is None else 1
        result = anchor + timedelta(weeks=interval * k)
        while after is not None and result <= after:
            k += 1
            result = anchor + timedelta(weeks=interval * k)
    if shift is None:
        shift = bool(spec.get("shift"))
    return _shifted(result, shift)


class PromiseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def run(fn, *a):
    try:
        return fn(*a)
    except PromiseError as error:
        raise HTTPException(422, {"error": error.code, "message": error.message})
    except OverflowError as error:
        raise HTTPException(429, str(error))


def mon(token: str) -> int:
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
        d, mon_i, raw = (int(m[1]), mon(m[2]), m[3]) if dmy else (int(m[2]), mon(m[1]), m[3])
        if raw:
            day = _date(_year(raw), mon_i, d)
        else:
            day = _date(today.year, mon_i, d)
            if day and day < today:
                day = _date(today.year + 1, mon_i, d)
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
    def __init__(self, buddy: Buddy, model_router=None, settings: Settings | None = None):
        self.buddy = buddy
        self.router = model_router
        self.settings = settings or Settings()
        self.dir = _data_dir(self.settings)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = self.dir / "promises.sqlite3"
        self._init_db()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ticked_day: date | None = None

    # ---- lifecycle ----------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="piblo-promises", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(60):
            try:
                self.tick()
            except Exception:  # the scheduler must never take the host down
                log.exception("promises scheduler tick failed")

    def _init_db(self) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS promise_rules (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    spec TEXT NOT NULL,
                    next_due TEXT,
                    active INTEGER NOT NULL DEFAULT 0,
                    alert_id TEXT,
                    created_at REAL NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS promise_alert_links (
                    alert_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL
                )"""
            )
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db)

    def _get_rule(self, rid: str) -> dict | None:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM promise_rules WHERE id = ?", (rid,)).fetchone()
        return dict(row) if row else None

    def _list_rules(self, active_only: bool = False) -> list[dict]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            if active_only:
                rows = conn.execute("SELECT * FROM promise_rules WHERE active = 1 ORDER BY created_at").fetchall()
            else:
                rows = conn.execute("SELECT * FROM promise_rules ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def _rule_view(self, r: dict) -> dict:
        spec = json.loads(r["spec"] or "{}")
        with self._conn() as conn:
            linked = conn.execute(
                "SELECT 1 FROM promise_alert_links WHERE rule_id = ? LIMIT 1", (r["id"],)
            ).fetchone() is not None
        return {
            "id": r["id"],
            "kind": r["kind"],
            "title": r["title"],
            "spec": spec,
            "next_due": r["next_due"],
            "active": bool(r["active"]),
            "alert_id": r["alert_id"],
            "desc": spec.get("desc") or r["title"],
            "shift": bool(spec.get("shift")),
            "pending": not bool(r["active"]) and linked,
        }

    # ---- ledger -------------------------------------------------------
    def _ledger(self, line: str) -> None:
        try:
            append_ledger("reminder", line)  # the one daily ledger (ledger.py)
        except (OSError, UnicodeError, ValueError):
            pass

    # ---- phrase parsing ----------------------------------------------
    @staticmethod
    def _json_block(reply: str) -> str:
        reply = reply.strip()
        if reply.startswith("```"):
            tail = reply.split("```", 2)
            reply = tail[1] if len(tail) > 1 else reply
            reply = reply.split("```", 1)[0]
        start = reply.find("{")
        end = reply.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise PromiseError("no_json", "The reminder could not be parsed. Say it again plainly.")
        return reply[start:end + 1]

    def _llm_parse(self, text: str) -> dict | None:
        """Local LLM (llm.py local route) parse, or None when unavailable."""
        if self.router is None:
            return None
        prompt = (
            "You parse a household reminder phrase into JSON and return ONLY JSON, no code fences. "
            "Fields: kind (\"bin\"|\"home\"), title (short name), frequency (\"weekly\"|\"fortnightly\"|\"weeks\"|\"fixed\"), "
            "interval_weeks (int, 1 for weekly, 2 for fortnightly), weekday (0=Mon .. 6=Sun or null), "
            "fixed_dates (ISO date strings for yearly anniversaries such as MOT or tax), "
            "shift_on_bank_holiday (bool), next_due (ISO date when the phrase names one, else null), "
            "desc (a short spoken summary).\n"
            "Examples:\n"
            "\"Black bin every other Tuesday, next one 18 Nov\" -> "
            "{\"kind\":\"bin\",\"title\":\"Black bin\",\"frequency\":\"fortnightly\",\"interval_weeks\":2,"
            "\"weekday\":1,\"shift_on_bank_holiday\":false,\"next_due\":\"2026-11-18\","
            "\"desc\":\"Black bin, fortnightly on Tuesdays\"}\n"
            "\"Recycling weekly Thursday\" -> "
            "{\"kind\":\"bin\",\"title\":\"Recycling\",\"frequency\":\"weekly\",\"interval_weeks\":1,"
            "\"weekday\":3,\"shift_on_bank_holiday\":false,\"next_due\":null,"
            "\"desc\":\"Recycling, weekly on Thursdays\"}\n"
            "\"Garden waste fortnightly Friday\" -> "
            "{\"kind\":\"bin\",\"title\":\"Garden waste\",\"frequency\":\"fortnightly\",\"interval_weeks\":2,"
            "\"weekday\":4,\"shift_on_bank_holiday\":false,\"next_due\":null}\n"
            "\"Car MOT 14 June\" -> "
            "{\"kind\":\"home\",\"title\":\"Car MOT\",\"frequency\":\"fixed\",\"interval_weeks\":1,"
            "\"weekday\":null,\"fixed_dates\":[\"2026-06-14\"],\"next_due\":\"2026-06-14\"}\n"
            "\"Prescription refills every 2 weeks\" -> "
            "{\"kind\":\"home\",\"title\":\"Prescription refills\",\"frequency\":\"weeks\","
            "\"interval_weeks\":2,\"weekday\":null,\"next_due\":null}\n"
            "Phrase: "
        )
        try:
            reply, _provider = self.router.chat(
                [{"role": "system", "content": prompt}, {"role": "user", "content": text}], "local"
            )
        except (OSError, RuntimeError, ValueError, RouteError):  # model down: fall back to the plain parser
            return None
        try:
            data = json.loads(self._json_block(reply))
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        kind = data.get("kind")
        if kind not in ("bin", "home"):
            return None
        title = (data.get("title") or "").strip() or "Reminder"
        freq = data.get("frequency", "weekly")
        if freq not in ("weekly", "fortnightly", "weeks", "fixed"):
            return None
        interval = max(1, int(data.get("interval_weeks", 2 if freq == "fortnightly" else 1)))
        weekday = data.get("weekday")
        weekday = None if weekday is None else int(weekday)
        if weekday is not None and not 0 <= weekday <= 6:
            return None
        fixed = data.get("fixed_dates") or []
        fixed = [str(d) for d in fixed if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d))]
        next_due = data.get("next_due")
        if next_due is not None:
            next_due = date.fromisoformat(str(next_due))
        desc = (data.get("desc") or "").strip() or None
        return {
            "kind": kind, "title": title[:60], "frequency": freq, "interval_weeks": interval,
            "weekday": weekday, "fixed_dates": fixed, "shift_on_bank_holiday": bool(data.get("shift_on_bank_holiday")),
            "next_due": next_due.isoformat() if next_due else None, "desc": desc,
        }

    def _free_parse(self, text: str, today: date) -> dict:
        """Deterministic fallback when the local model is unavailable."""
        low = text.lower()
        freq = "weekly"
        interval = 1
        weekday: int | None = None
        m = re.search(r"\bevery\s+(\d+)\s+weeks?\b", low)
        if m:
            m2 = _DAY_RE.search(low)
            if m2:
                freq, interval, weekday = "weeks", int(m.group(1)), WKDAY[m2.group(1)]
        else:
            m = _DAY_RE.search(low)
            if m and re.search(r"\b(every other|fortnightly)\b", low[: m.start()]):
                freq, interval, weekday = "fortnightly", 2, WKDAY[m.group(1)]
            elif m:
                freq, interval, weekday = "weekly", 1, WKDAY[m.group(1)]
        fixed_dates: list[str] = []
        if re.search(r"\b(mot|tax|boiler|service|prescription|smoke alarm)\b", low) and weekday is None:
            freq = "fixed"
        next_due: date | None = None
        month_re = "|".join(sorted({m for n in _MONTHS for m in (n, n[:3])}, key=len, reverse=True))
        m = re.search(r"\bnext one\s+(\d{1,2})\s+(" + month_re + r")\b", low) or \
            re.search(r"\bon\s+(\d{1,2})\s+(" + month_re + r")\b", low) or _DMY.search(low)
        if m:
            if m.re is _DMY:
                d, mon_i, raw = int(m[1]), mon(m[2]), m[3]
                cand = date(_year(raw) if raw else today.year, mon_i, d)
                if not raw and cand < today:
                    cand = date(today.year + 1, mon_i, d)
            else:
                cand = date(today.year, mon(m.group(2)), int(m.group(1)))
                if cand < today:
                    cand = date(today.year + 1, mon(m.group(2)), int(m.group(1)))
            next_due = cand
        if next_due:
            fixed_dates = [next_due.isoformat()]
        title = re.sub(
            r"\b(every other|fortnightly|weekly|every \d+ weeks?|next one|on)\b|\d{1,2} (?:" + month_re + r")|" + _DAY_RE.pattern,
            " ", low,
        )
        title = re.sub(r"\s+", " ", title).strip(" -.,").title()[:60] or "Reminder"
        kind = "bin" if "bin" in low else "home"
        if freq == "fixed":
            when = ", ".join(d.isoformat() for d in ([] if next_due is None else [next_due])) or "yearly"
            desc = f"{title}, {when}"
        else:
            on = f" on {WKNAME[weekday]}s" if weekday is not None else ""
            desc = f"{title}, {freq}" + ("" if freq == "weeks" else on) + (
                f" every {interval} weeks" if interval > 1 else ""
            )
        return {
            "kind": kind, "title": title, "frequency": freq, "interval_weeks": interval, "weekday": weekday,
            "fixed_dates": fixed_dates, "shift_on_bank_holiday": False,
            "next_due": next_due.isoformat() if next_due else None, "desc": desc,
        }

    def parse_phrase(self, text: str, today: date | None = None) -> dict:
        """Parse a spoken reminder into a rule draft. Never saves anything."""
        today = today or _today_local()
        draft = self._llm_parse(text)
        if draft is None:
            draft = self._free_parse(text, today)
        if draft["frequency"] != "fixed" and draft["weekday"] is None:
            raise PromiseError("no_date", "I need a weekday, for example 'every other Tuesday'.")
        spec = {
            "frequency": draft["frequency"], "interval_weeks": draft["interval_weeks"],
            "weekday": draft["weekday"], "fixed_dates": draft["fixed_dates"],
            "shift": draft["shift_on_bank_holiday"],
        }
        if draft["next_due"]:
            spec["anchor"] = draft["next_due"]
        elif draft["frequency"] != "fixed":
            spec["anchor"] = _weekday_on_or_after(draft["weekday"], today).isoformat()
        if draft["frequency"] == "fixed" and not draft["fixed_dates"]:
            raise PromiseError("no_date", "I need a date, for example 'car MOT 14 June'.")
        draft["anchor"] = spec.get("anchor")
        draft["spec"] = spec
        draft["desc"] = draft["desc"] or draft["title"]
        return draft

    # ---- rules --------------------------------------------------------
    def _first_due(self, spec: dict) -> date:
        if spec["frequency"] == "fixed":
            best = None
            for iso in spec["fixed_dates"]:
                _, m, dd = iso.split("-")
                cand = date(_today_local().year, int(m), int(dd))
                while cand <= _today_local():
                    cand = date(cand.year + 1, int(m), int(dd))
                if best is None or cand < best:
                    best = cand
            return best
        return _weekday_on_or_after(int(spec["weekday"]), _today_local())

    def create_rule_draft(self, draft: dict) -> dict:
        """Stage a rule and ask the Cardputer to confirm before saving."""
        rid = "pr-" + uuid.uuid4().hex[:12]
        spec = draft["spec"]
        anchor = date.fromisoformat(spec["anchor"]) if spec.get("anchor") else None
        first = anchor or self._first_due(spec)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO promise_rules (id, kind, title, spec, next_due, active, alert_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (rid, draft["kind"], draft["title"], json.dumps(spec),
                 first.isoformat() if anchor else None, None, time.time()),
            )
            conn.commit()
        body = f"{draft['title']} — {draft['desc']}. Enter on your Piblo to save it."
        res = self.buddy.post_alert(AlertIn(
            source=SOURCE, kind="reminder", title="Save reminder?", body=body[:200],
            actions=["ack"], delay_seconds=0, idempotency_key="promise-draft-" + rid,
        ))
        with self._conn() as conn:
            conn.execute("INSERT INTO promise_alert_links (alert_id, rule_id) VALUES (?, ?)", (res["id"], rid))
            conn.commit()
        self._ledger(f"REMINDER DRAFT {draft['kind']}: {draft['title']} — {draft['desc']}")
        return {"id": rid, "pending": True, "alert_id": res["id"],
                "first_due": first.isoformat(),
                "spoken": f"I've drafted {draft['desc']}. Press Enter on your Piblo to save it."}

    def create_rule(self, text: str, kind: str | None = None, confirmed: bool = False) -> dict:
        """Parse + save (confirmed) or stage for Cardputer confirmation."""
        draft = self.parse_phrase(text)
        if kind:
            draft["kind"] = kind
        if confirmed:
            rid = "pr-" + uuid.uuid4().hex[:12]
            spec = draft["spec"]
            first = date.fromisoformat(spec["anchor"]) if spec.get("anchor") else self._first_due(spec)
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO promise_rules (id, kind, title, spec, next_due, active, alert_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, NULL, ?)",
                    (rid, draft["kind"], draft["title"], json.dumps(spec), first.isoformat(), time.time()),
                )
                conn.commit()
            self._ledger(f"REMINDER SAVED {draft['kind']}: {draft['title']} — {draft['desc']}")
            return {"id": rid, "active": True, "next_due": first.isoformat(),
                    "spoken": f"Saved: {draft['desc']}."}
        return self.create_rule_draft(draft)

    def update_rule(self, rid: str, patch: dict) -> dict:
        rule = self._get_rule(rid)
        if rule is None:
            raise PromiseError("unknown_reminder", "That reminder is not saved on this Piblo.")
        fields = []
        values = []
        for key in ("title", "next_due", "active"):
            if key in patch:
                fields.append(f"{key} = ?")
                values.append(patch[key])
        if "shift" in patch:
            spec = json.loads(rule["spec"])
            spec["shift"] = bool(patch["shift"])
            fields.append("spec = ?")
            values.append(json.dumps(spec))
        if not fields:
            return self._rule_view(rule)
        values.append(rid)
        with self._conn() as conn:
            conn.execute(f"UPDATE promise_rules SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        return self._rule_view(self._get_rule(rid))

    def delete_rule(self, rid: str) -> dict:
        rule = self._get_rule(rid)
        if rule is None:
            raise PromiseError("unknown_reminder", "That reminder is not saved on this Piblo.")
        with self._conn() as conn:
            conn.execute("DELETE FROM promise_alert_links WHERE rule_id = ?", (rid,))
            conn.execute("DELETE FROM promise_rules WHERE id = ?", (rid,))
            conn.commit()
        return {"ok": True, "id": rid}

    # ---- scheduler: 7pm night-before nudge ----------------------------
    def tick(self) -> None:
        local = datetime.now(tz())
        day = local.date()
        if self._ticked_day == day or local.hour != 19:
            return
        self._ticked_day = day
        tomorrow = (day + timedelta(days=1)).isoformat()
        due = [r for r in self._list_rules(active_only=True) if r["next_due"] == tomorrow]
        if not due:
            return
        titles = " + ".join(r["title"] for r in due)
        res = self.buddy.post_alert(AlertIn(
            source=SOURCE, kind="reminder", title="Home reminder",
            body=f"Tomorrow: {titles}"[:200], actions=["ack"], delay_seconds=0,
            idempotency_key="pd-" + sha1((tomorrow + "|" + titles).encode()).hexdigest()[:16],
        ))
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO promise_alert_links (alert_id, rule_id) VALUES (?, ?)",
                [(res["id"], r["id"]) for r in due],
            )
            conn.commit()
        self._ledger("REMINDER DUE TOMORROW " + titles)

    # ---- Cardputer ack hook (called from /buddy/alert/{aid}/ack) ------
    def on_alert_acked(self, alert_id: str) -> None:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            links = conn.execute(
                "SELECT rule_id FROM promise_alert_links WHERE alert_id = ?", (alert_id,)
            ).fetchall()
        if not links:
            return
        for row in links:
            rule = self._get_rule(row["rule_id"])
            if rule is None:
                continue
            if alert_id.startswith("promise-draft-"):
                self._activate_rule(rule)
            elif alert_id.startswith("pd-"):
                self._occurrence_done(rule)

    def _activate_rule(self, rule: dict) -> None:
        spec = json.loads(rule["spec"])
        first = date.fromisoformat(spec["anchor"]) if spec.get("anchor") else self._first_due(spec)
        with self._conn() as conn:
            conn.execute("UPDATE promise_rules SET active = 1, next_due = ? WHERE id = ?",
                         (first.isoformat(), rule["id"]))
            conn.commit()
        desc = spec.get("desc") or rule["title"]
        self._ledger(f"REMINDER SAVED {rule['kind']}: {rule['title']} — {desc}")

    def _occurrence_done(self, rule: dict) -> None:
        spec = json.loads(rule["spec"])
        old = date.fromisoformat(rule["next_due"]) if rule["next_due"] else None
        nxt = next_date(spec, after=old)
        with self._conn() as conn:
            conn.execute("UPDATE promise_rules SET next_due = ? WHERE id = ?", (nxt.isoformat(), rule["id"]))
            conn.commit()
        self._ledger(f"REMINDER DONE {rule['kind']}: {rule['title']} -> next {nxt.isoformat()}")

    # ---- the original one-off Promise Keeper flow (unchanged behaviour) ----
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
                waiting.append(r)  # scheduled, or snoozed ("later")
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

    # ---- queries ------------------------------------------------------
    def upcoming(self, days: int = 14) -> dict:
        today = _today_local()
        limit = today + timedelta(days=days - 1)
        by_date: dict[str, list[dict]] = {}
        for r in self._list_rules(active_only=True):
            spec = json.loads(r["spec"] or "{}")
            d = date.fromisoformat(r["next_due"]) if r["next_due"] else None
            if d is None:
                d = next_date(spec)
            while d < today:
                d = next_date(spec, after=d)
            seen = set()
            cur = d
            while cur <= limit and cur not in seen:
                seen.add(cur)
                by_date.setdefault(cur.isoformat(), []).append(
                    {"id": r["id"], "kind": r["kind"], "title": r["title"],
                     "shift": bool(spec.get("shift"))}
                )
                cur = next_date(spec, after=cur)
        return {"ok": True, "by_date": by_date, "days": days}

    def next_spoken(self, span: int = 7) -> str:
        by_date = self.upcoming(span).get("by_date", {})
        if not by_date:
            return "No home reminders coming up"
        today = _today_local()
        parts = []
        for d in sorted(by_date):
            dd = date.fromisoformat(d)
            label_ = "today" if dd == today else (
                "tomorrow" if dd == today + timedelta(days=1) else f"{WKNAME[dd.weekday()]} {dd.day} {dd:%b}"
            )
            parts.append(f"{label_}: " + ", ".join(f"{x['title']} ({x['kind']})" for x in by_date[d]))
        return "Next reminders: " + "; ".join(parts)

    # ---- voice/app entry points ---------------------------------------
    def try_setup(self, text: str) -> str | None:
        """Setup phrase by voice or app -> staged rule + Cardputer confirmation.
        Returns the spoken reply, or None when the phrase is not a recurring setup."""
        low = text.lower()
        if not re.search(r"\b(every|weekly|fortnightly|each)\b", low):
            return None
        try:
            draft = self.parse_phrase(text)
        except (PromiseError, ValueError, KeyError):
            return None
        result = self.create_rule_draft(draft)
        return result["spoken"]

    def answer_question(self, text: str) -> str | None:
        """Spoken answer to "which bin is it?"-style questions, else None."""
        low = text.lower()
        words = ("bin", "recycling", "rubbish", "refuse", "reminder", "mot", "tax",
                 "prescription", "boiler", "smoke alarm")
        if not any(w in low for w in words):
            return None
        if "?" not in low and not any(w in low.split() for w in ("which", "what", "when", "next", "upcoming", "today", "tomorrow")):
            return None
        return self.next_spoken()


class Propose(BaseModel):
    note: str = Field(min_length=1, max_length=140)


class Confirm(Propose):
    due: str = Field(min_length=16, max_length=40)
    confirmed: bool    # the exact timestamp was shown to the user and they accepted it


class RuleIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    kind: str | None = None
    confirmed: bool = False


class RulePatch(BaseModel):
    title: str | None = None
    shift: bool | None = None
    active: bool | None = None
    next_due: str | None = None


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

    # -- recurring bin-day / home rules --
    class RuleIn(BaseModel):
        text: str = Field(min_length=1, max_length=200)
        kind: str | None = None
        confirmed: bool = False

    class RulePatch(BaseModel):
        title: str | None = None
        shift: bool | None = None
        active: bool | None = None
        next_due: str | None = None

    @router.get("/rules")
    def rules_route():
        return run(lambda: {"ok": True, "rules": [service._rule_view(r) for r in service._list_rules()]})

    @router.post("/rules")
    def create_rule_route(body: RuleIn = Body(...)):
        return run(lambda: service.create_rule(body.text, body.kind, body.confirmed))

    @router.patch("/rules/{rid}")
    def patch_rule_route(rid: str, body: RulePatch):
        return run(lambda: service.update_rule(rid, body.model_dump(exclude_none=True)))

    @router.delete("/rules/{rid}")
    def delete_rule_route(rid: str):
        return run(lambda: service.delete_rule(rid))

    @router.get("/upcoming")
    def upcoming_route(days: int = 14):
        return run(lambda: service.upcoming(days))

    @router.get("/next")
    def next_route():
        return {"text": service.next_spoken()}

    @router.post("/tick")
    def tick_route():
        service.tick()
        return {"ok": True}

    return router
