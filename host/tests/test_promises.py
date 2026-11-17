"""Promise Keeper: deterministic British date proposals, confirm-only saving, Today cards, restart safety."""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost import promises
from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.buddy import Buddy
from mazhost.buddy_store import BuddyStore
from mazhost.promises import BANK_HOLIDAYS, PromiseError, Promises, build_promises_router, next_date, propose

NOW = datetime(2027, 1, 4, 12, 0, tzinfo=timezone.utc)   # Monday


def due_of(note, now=NOW):
    return [c["due"] for c in propose(note, now)["candidates"]]


def test_ambiguous_numeric_date_offers_both_readings_british_first():
    out = propose("send the invoice by 03/04/2027", NOW)
    assert out["ambiguous"] and out["confirm_required"] and out["default_time"]
    assert [c["due"] for c in out["candidates"]] == ["2027-04-03T09:00", "2027-03-04T09:00"]
    assert out["candidates"][0]["label"] == "Sat 3 Apr 2027, 09:00 BST"


def test_unambiguous_and_named_dates():
    assert due_of("call back 25/12/2027 at 5pm") == ["2027-12-25T17:00"]
    assert due_of("reply by 3rd April 2027 17:30") == ["2027-04-03T17:30"]
    assert due_of("book it, April 3") == ["2027-04-03T09:00"]
    assert due_of("tomorrow at 8am") == ["2027-01-05T08:00"]
    assert due_of("in 3 hours") == ["2027-01-04T15:00"]
    assert due_of("5 January") == ["2027-01-05T09:00"]
    assert due_of("2 January") == ["2028-01-02T09:00"]   # already passed this year: next occurrence


@pytest.mark.parametrize("note,code", [
    ("by 31/02/2027", "invalid_date"), ("by 30 February", "invalid_date"),
    ("by 03/04/1999", "invalid_year"), ("by 03/04/2101", "invalid_year"),
    ("sort it out", "no_date"), ("today at 8am", "in_the_past"),
    ("tomorrow at 13pm", "invalid_time"),
])
def test_bad_input_is_rejected_never_invented(note, code):
    with pytest.raises(PromiseError) as e:
        propose(note, NOW)
    assert e.value.code == code


def test_next_friday_is_ambiguous_until_this_weeks_friday_has_passed():
    assert due_of("next friday") == ["2027-01-08T09:00", "2027-01-15T09:00"]   # Monday: both readings
    assert due_of("friday") == ["2027-01-08T09:00"]
    sat = datetime(2027, 1, 9, 12, 0, tzinfo=timezone.utc)
    assert due_of("next friday", sat) == ["2027-01-15T09:00"]


def test_dst_labels_and_skipped_hour():
    wed = datetime(2027, 3, 24, 12, 0, tzinfo=timezone.utc)
    out = propose("next friday at 9am", wed)
    assert [c["label"] for c in out["candidates"]] == ["Fri 26 Mar 2027, 09:00 GMT", "Fri 2 Apr 2027, 09:00 BST"]
    with pytest.raises(PromiseError) as e:      # 01:30 on 28 Mar 2027 never happens in the UK
        propose("on 28/03/2027 at 1:30am", wed)
    assert e.value.code == "nonexistent_time"


@pytest.fixture
def stack(tmp_path):
    path = tmp_path / "buddy.sqlite3"

    def make():
        buddy = Buddy(store=BuddyStore(path))
        app = FastAPI()
        app.include_router(build_promises_router(Promises(buddy)))
        return TestClient(app), buddy

    return make


def future(seconds):
    return datetime.fromtimestamp(time.time() + seconds, promises.tz()).strftime("%Y-%m-%dT%H:%M")


def confirm(c, note="send the invoice", due=None, **kw):
    return c.post("/promises/confirm", json={"note": note, "due": due or future(7200), "confirmed": True, **kw})


def test_nothing_is_saved_without_confirmation(stack):
    c, _ = stack()
    assert c.post("/promises/propose", json={"note": "invoice by tomorrow"}).status_code == 200
    assert c.get("/promises/today").json()["empty"]
    assert confirm(c, confirmed=False).status_code == 422
    assert c.post("/promises/confirm", json={"note": "x", "due": future(7200)}).status_code == 422
    assert c.get("/promises/today").json()["counts"] == {"due": 0, "waiting": 0, "done": 0}
    bad = c.post("/promises/propose", json={"note": "by 31/02/2027"})
    assert bad.status_code == 422 and bad.json()["detail"]["error"] == "invalid_date"


def test_confirm_echoes_exact_time_and_rejects_past_and_too_far(stack):
    c, _ = stack()
    ok = confirm(c, due="2099-01-01T09:00")
    assert ok.status_code == 422 and ok.json()["detail"]["error"] == "too_far"
    assert confirm(c, due="2020-01-01T09:00").json()["detail"]["error"] == "in_the_past"
    assert confirm(c, due="nonsense-at-all!!").json()["detail"]["error"] == "invalid_date"
    res = confirm(c).json()
    assert res["ok"] and res["due_label"].endswith(("GMT", "BST")) and not res["duplicate"]


def test_duplicate_click_and_note_make_one_promise(stack):
    c, _ = stack()
    due = future(7200)
    first, second = confirm(c, due=due).json(), confirm(c, due=due).json()
    assert second["duplicate"] and second["id"] == first["id"]
    assert confirm(c, due=future(9000)).json()["id"] != first["id"]   # same words, different confirmed time
    assert c.get("/promises/today").json()["counts"]["waiting"] == 2


def test_flow_due_done_undo_later_and_cards(stack, monkeypatch):
    c, buddy = stack()
    pid = confirm(c, note="send the invoice to the accountant today please", due=future(3600)).json()["id"]
    today = c.get("/promises/today").json()
    card = today["cards"][0]
    assert card["state"] == "waiting" and card["id"] == pid and len(card["title"]) <= 28 and len(card["when"]) <= 16
    assert card["note"] == "send the invoice to the accountant today please"   # original words kept
    # Clock moves on (also covers drift): the stored deadline fires, no external trigger needed.
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3700)
    due = c.get("/promises/today").json()
    assert due["cards"][0]["state"] == "due" and due["cards"][0]["overdue"] and due["counts"]["due"] == 1
    assert c.post(f"/promises/{pid}/later", json={"minutes": 10}).json()["outcome"] == "snoozed"
    assert c.get("/promises/today").json()["cards"][0]["state"] == "waiting"
    monkeypatch.setattr(time, "time", lambda: real() + 3700 + 700)
    assert c.get("/promises/today").json()["cards"][0]["state"] == "due"   # later ran out: nags again
    assert c.post(f"/promises/{pid}/done").json()["outcome"] == "acked"
    assert c.post(f"/promises/{pid}/done").json()["outcome"] == "acked"       # duplicate tap is harmless
    done = c.get("/promises/today").json()
    assert done["cards"][0]["state"] == "done" and done["cards"][0]["actions"] == ["undo"]
    assert c.post(f"/promises/{pid}/undo").json()["outcome"] is None
    assert c.post(f"/promises/{pid}/undo").status_code == 400                 # nothing left to undo
    assert c.get("/promises/today").json()["cards"][0]["state"] == "due"


def test_three_cards_and_unknown_or_foreign_ids(stack, monkeypatch):
    c, buddy = stack()
    a = confirm(c, note="a", due=future(90)).json()["id"]
    b = confirm(c, note="b", due=future(120)).json()["id"]
    confirm(c, note="c", due=future(86400))
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 200)
    c.post(f"/promises/{b}/done")
    states = [x["state"] for x in c.get("/promises/today").json()["cards"]]
    assert states == ["due", "waiting", "done"]
    assert c.post("/promises/al-nope/done").status_code == 404
    from mazhost.buddy import AlertIn
    foreign = buddy.post_alert(AlertIn(source="ci", title="build failed"))["id"]
    assert c.post(f"/promises/{foreign}/done").status_code == 404           # only promises, never other alerts
    assert a


def test_survives_core_restart_without_losing_or_renagging(stack, monkeypatch):
    c, _ = stack()
    pending = confirm(c, note="still to do", due=future(3600)).json()["id"]
    finished = confirm(c, note="already done", due=future(90)).json()["id"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 120)
    c.get("/promises/today")
    c.post(f"/promises/{finished}/done")
    c2, buddy2 = stack()                         # Core reboot: new objects, same sqlite file
    cards = {x["id"]: x["state"] for x in c2.get("/promises/today").json()["cards"]}
    assert cards == {pending: "waiting", finished: "done"}
    assert confirm(c2, note="still to do", due=future(3600)).status_code == 200   # no crash, keys survive too
    monkeypatch.setattr(time, "time", lambda: real() + 3700)
    assert {x["id"]: x["state"] for x in c2.get("/promises/today").json()["cards"]}[pending] == "due"
    events = [e for e in buddy2.events()["events"] if e["kind"] == "alert"]
    assert len([e for e in events if e["alert"]["id"] == pending]) == 1   # fired once, not per restart


def test_empty_today_and_core_app_mounts_it():
    from mazhost.app import create_app
    from mazhost.config import Settings
    app = TestClient(create_app(Settings(token="test-token-that-is-not-default", _env_file=None)))
    assert app.get("/promises/today").status_code == 401    # behind the same bearer token
    r = app.get("/promises/today", headers={"Authorization": "Bearer test-token-that-is-not-default"})
    assert r.status_code == 200 and r.json()["empty"] and r.json()["cards"] == []


# ---- review round 1 regressions

def test_restart_keeps_due_and_fired_for_a_fired_unanswered_promise(stack, monkeypatch):
    c, _ = stack()
    pid = confirm(c, note="ring the plumber", due=future(3600)).json()["id"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3700)
    before = c.get("/promises/today").json()["cards"][0]
    assert before["state"] == "due"
    c2, buddy2 = stack()
    after = c2.get("/promises/today").json()["cards"][0]
    assert after["state"] == "due" and after["when"] == before["when"] and after["id"] == pid
    rec = next(r for r in buddy2.alerts()["alerts"] if r["id"] == pid)
    assert rec["fired"] and abs(rec["due"] - (real() + 3600)) < 120      # the real deadline, not the created time


def test_done_early_then_restart_then_undo_still_fires_later(stack, monkeypatch):
    c, _ = stack()
    pid = confirm(c, note="early bird", due=future(3600)).json()["id"]
    c.post(f"/promises/{pid}/done")
    c2, _ = stack()
    assert c2.get("/promises/today").json()["cards"][0]["state"] == "done"
    assert c2.post(f"/promises/{pid}/undo").json()["outcome"] is None
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3700)
    assert c2.get("/promises/today").json()["cards"][0]["state"] == "due"


def test_alert_table_full_is_429_not_500(stack):
    c, _ = stack()
    for i in range(50):
        assert confirm(c, note=f"n{i}", due=future(3600 + i * 60)).status_code == 200
    r = confirm(c, note="one too many", due=future(9000))
    assert r.status_code == 429 and r.json()["detail"] == "alert_table_full"


def test_past_uk_reading_is_never_swapped_for_the_us_one():
    with pytest.raises(PromiseError) as e:      # 03/02/2027 is 3 Feb (past); US reading 2 Mar is still ahead
        propose("by 03/02/2027", datetime(2027, 2, 10, 12, 0, tzinfo=timezone.utc))
    assert e.value.code == "in_the_past"
    assert due_of("by 13/02/2027", datetime(2027, 2, 10, 12, 0, tzinfo=timezone.utc)) == ["2027-02-13T09:00"]


def test_in_n_days_and_weeks_use_the_calendar_day_and_a_stated_time():
    assert due_of("in 3 days") == ["2027-01-07T09:00"]
    assert due_of("in 2 weeks at 5pm") == ["2027-01-18T17:00"]
    # Across the clock change the wall time holds (09:00 BST), it does not drift by an hour.
    wed = datetime(2027, 3, 24, 12, 0, tzinfo=timezone.utc)
    assert due_of("in 7 days", wed) == ["2027-03-31T09:00"]
    assert propose("in 7 days", wed)["candidates"][0]["label"].endswith("BST")


def test_today_without_a_time_after_nine_defaults_to_five_pm_then_an_hour_on():
    noon = datetime(2027, 1, 4, 12, 0, tzinfo=timezone.utc)
    out = propose("sort the invoice today", noon)
    assert [c["due"] for c in out["candidates"]] == ["2027-01-04T17:00"] and out["default_time"]
    evening = datetime(2027, 1, 4, 18, 30, tzinfo=timezone.utc)
    assert due_of("sort the invoice today", evening) == ["2027-01-04T19:30"]
    early = datetime(2027, 1, 4, 7, 0, tzinfo=timezone.utc)
    assert due_of("sort the invoice today", early) == ["2027-01-04T09:00"]
    with pytest.raises(PromiseError):           # a stated time that has passed is still rejected
        propose("today at 8am", noon)


def test_in_n_hours_is_elapsed_time_across_the_clock_change():
    # 00:30 GMT on 28 Mar 2027 + 2h elapsed = 03:30 BST (01:30 never happens).
    now = datetime(2027, 3, 28, 0, 30, tzinfo=timezone.utc)
    assert due_of("in 2 hours", now) == ["2027-03-28T03:30"]


# ---- Bin day + home reminders (recurring rules) ----

class FakeBuddy(Buddy):
    def __init__(self) -> None:
        super().__init__()
        self.posted = []

    def post_alert(self, a) -> dict:
        self.posted.append(a)
        return {"ok": True, "routed": "alert", "id": a.idempotency_key or "al-test", "scheduled": False}

    def posted_alerts(self) -> dict:
        return {"posts": self.posted}

    def alert_outcome(self, aid: str, action: str, minutes: int = 0) -> dict:
        return {"ok": True, "id": aid, "outcome": action}


def test_fortnightly_maths():
    spec = {"frequency": "fortnightly", "interval_weeks": 2, "weekday": 1, "anchor": "2026-11-18",
            "fixed_dates": [], "shift": False}
    assert next_date(spec) == date(2026, 11, 18)
    assert next_date(spec, after=date(2026, 11, 18)) == date(2026, 12, 2)
    assert next_date(spec, after=date(2026, 12, 2)) == date(2026, 12, 16)


def test_weekly_rolls():
    spec = {"frequency": "weekly", "interval_weeks": 1, "weekday": 0, "anchor": "2026-11-16",
            "fixed_dates": [], "shift": False}  # Monday
    assert next_date(spec, after=date(2026, 11, 16)) == date(2026, 11, 23)


def test_fixed_yearly():
    spec = {"frequency": "fixed", "interval_weeks": 1, "weekday": None, "anchor": None,
            "fixed_dates": ["2026-06-14"], "shift": False}
    assert next_date(spec, after=date(2026, 6, 14)) == date(2027, 6, 14)


def test_bank_holiday_shift():
    from mazhost.promises import BANK_HOLIDAYS
    shifted = {"frequency": "weekly", "interval_weeks": 1, "weekday": 0, "anchor": "2026-04-27",
               "fixed_dates": [], "shift": True}
    assert date(2026, 5, 4) in BANK_HOLIDAYS  # early May bank holiday, a Monday
    assert next_date(shifted, after=date(2026, 4, 27)) == date(2026, 5, 5)
    unshifted = dict(shifted, shift=False)
    assert next_date(unshifted, after=date(2026, 4, 27)) == date(2026, 5, 4)


def test_phrase_parse_deterministic(tmp_path):
    d = Promises(FakeBuddy(), settings=Settings(data_dir=str(tmp_path))).parse_phrase("Black bin every other Tuesday, next one 18 Nov")
    assert d["kind"] == "bin"
    assert d["title"] == "Black Bin"
    assert d["spec"]["frequency"] == "fortnightly"
    assert d["spec"]["weekday"] == 1  # Tuesday
    assert d["spec"]["anchor"] == "2026-11-18"


def test_phrase_parse_recycling(tmp_path):
    d = Promises(FakeBuddy(), settings=Settings(data_dir=str(tmp_path))).parse_phrase("Recycling weekly Thursday")
    assert d["spec"]["frequency"] == "weekly"
    assert d["spec"]["weekday"] == 3  # Thursday


def test_phrase_parse_home_fixed(tmp_path):
    d = Promises(FakeBuddy(), settings=Settings(data_dir=str(tmp_path))).parse_phrase("Car MOT 14 June")
    assert d["kind"] == "home"
    assert d["spec"]["frequency"] == "fixed"
    expected = date(2026, 6, 14)
    if expected < date.today():
        expected = date(2027, 6, 14)
    assert d["spec"]["fixed_dates"] == [expected.isoformat()]


def test_phrase_parse_llm_preferred(tmp_path):
    class FakeRouter:
        def chat(self, messages, route):
            return (
                '{"kind":"bin","title":"Recycling","frequency":"weekly","interval_weeks":1,'
                '"weekday":3,"next_due":null,"desc":"Recycling, weekly on Thursdays"}',
                "local",
            )

    engine = Promises(FakeBuddy(), model_router=FakeRouter(), settings=Settings(data_dir=str(tmp_path)))
    d = engine.parse_phrase("Recycling weekly Thursday")
    assert d["title"] == "Recycling"
    assert d["spec"]["frequency"] == "weekly"


def test_create_rule_then_cardputer_confirmation(tmp_path):
    buddy = FakeBuddy()
    engine = Promises(buddy, settings=Settings(data_dir=str(tmp_path)))
    res = engine.create_rule("Black bin every other Tuesday, next one 18 Nov")
    assert res["pending"] is True
    assert buddy.posted  # confirmation alert sent to the Cardputer
    rules = [engine._rule_view(r) for r in engine._list_rules()]
    assert len(rules) == 1 and rules[0]["active"] is False and rules[0]["pending"] is True
    engine.on_alert_acked(res["alert_id"])  # Enter pressed on the Cardputer
    rule = engine._get_rule(rules[0]["id"])
    assert rule["active"]
    assert rule["next_due"] == "2026-11-18"


def test_create_rule_confirmed_directly(tmp_path):
    engine = Promises(FakeBuddy(), settings=Settings(data_dir=str(tmp_path)))
    res = engine.create_rule("Recycling weekly Thursday", confirmed=True)
    assert res["active"] is True
    rule = engine._get_rule(res["id"])
    d = date.today()
    while d.weekday() != 3:  # Thursday
        d = date.fromordinal(d.toordinal() + 1)
    assert rule["next_due"] == d.isoformat()


def test_occurrence_done_advances_fortnightly(tmp_path):
    engine = Promises(FakeBuddy(), settings=Settings(data_dir=str(tmp_path)))
    engine.create_rule("Black bin every other Tuesday, next one 18 Nov", confirmed=True)
    rule = engine._list_rules(active_only=True)[0]
    engine._occurrence_done(rule)
    rule = engine._get_rule(rule["id"])
    assert rule["next_due"] == "2026-12-02"


def test_upcoming_next_14_days(tmp_path):
    engine = Promises(FakeBuddy(), settings=Settings(data_dir=str(tmp_path)))
    engine.create_rule("Black bin every other Tuesday, next one 18 Nov", confirmed=True)
    engine.create_rule("Recycling weekly Thursday", confirmed=True)
    data = engine.upcoming(14)
    today = date.today()
    limit = today + timedelta(days=13)
    keys = sorted(data["by_date"])
    assert today.isoformat() <= keys[0]
    assert keys[-1] <= limit.isoformat()
    d = today
    while d.weekday() != 3:
        d = date.fromordinal(d.toordinal() + 1)
    last_thu = d
    while last_thu + timedelta(days=7) <= limit:
        last_thu = last_thu + timedelta(days=7)
    assert keys[-1] == last_thu.isoformat()
    assert any("Black Bin" in x["title"] for x in data["by_date"]["2026-11-18"])


def test_ledger_line_written(tmp_path, monkeypatch):
    monkeypatch.setenv("PIBLO_LEDGER_DIR", str(tmp_path / "ledger"))
    engine = Promises(FakeBuddy(), settings=Settings(data_dir=str(tmp_path)))
    engine.create_rule("Recycling weekly Thursday")
    files = list((tmp_path / "ledger").glob("*.md"))
    assert files, "no daily ledger file was written"
    assert "REMINDER DRAFT" in files[0].read_text(encoding="utf-8")


def test_rules_api_endpoints():
    settings_kwargs = {"token": "test-token-that-is-not-default", "_env_file": None}
    app = create_app(settings=Settings(**settings_kwargs))
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token-that-is-not-default"}
    r = client.post("/promises/rules", headers=headers,
                    json={"text": "Recycling weekly Thursday", "confirmed": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] is True and "next_due" in body
    rid = body["id"]
    r = client.get("/promises/rules", headers=headers)
    assert r.status_code == 200
    assert any(x["id"] == rid for x in r.json()["rules"])
    r = client.patch(f"/promises/rules/{rid}", headers=headers, json={"shift": True})
    assert r.status_code == 200 and r.json()["shift"] is True
    r = client.get("/promises/upcoming?days=14", headers=headers)
    assert r.status_code == 200 and "by_date" in r.json()
    r = client.get("/promises/next", headers=headers)
    assert r.status_code == 200 and isinstance(r.json()["text"], str)
    r = client.delete(f"/promises/rules/{rid}", headers=headers)
    assert r.status_code == 200
    r = client.get("/promises/rules", headers=headers)
    assert not any(x["id"] == rid for x in r.json()["rules"])



def test_rule_falls_back_to_plain_parser_when_local_model_is_down(tmp_path, monkeypatch):
    # Regression: a RouteError from the model router escaped _llm_parse, so POST /promises/rules
    # answered 503 LOCAL_UNAVAILABLE on any PC without a working local model.
    from mazhost.errors import ErrorCode, RouteError

    class DownRouter:
        def chat(self, *a, **k):
            raise RouteError(ErrorCode.LOCAL_UNAVAILABLE, "local", retryable=True)

    monkeypatch.setenv("PIBLO_LEDGER_DIR", str(tmp_path / "ledger"))
    engine = Promises(FakeBuddy(), model_router=DownRouter(), settings=Settings(data_dir=str(tmp_path)))
    assert engine._llm_parse("Recycling weekly Thursday") is None
    rule = engine.create_rule("Recycling weekly Thursday", None, True)
    assert rule["active"] is True
