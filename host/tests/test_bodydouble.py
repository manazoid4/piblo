from datetime import datetime

from mazhost.bodydouble import BodyDouble
from mazhost.buddy import Buddy
from mazhost.ledger import append_ledger


def make(tmp_path, title="invoice.xlsx - Excel"):
    log = []
    bd = BodyDouble(Buddy(), tmp_path / "bd.json", ledger=lambda k, t: log.append((k, t)), title_fn=lambda: title)
    return bd, log


def titles(bd):
    return [a["title"] for a in bd.buddy.alerts()["alerts"]]


def boom():
    raise AssertionError("must not read the window title")


def test_disabled_does_nothing(tmp_path):
    bd, _ = make(tmp_path)
    assert bd.tick(0) is None and bd.tick(10_000) is None and titles(bd) == []


def test_schedule_waits_one_interval_then_repeats(tmp_path):
    bd, _ = make(tmp_path)
    bd.update({"enabled": True, "mode": "voice", "interval_min": 25})
    assert bd.tick(0) is None            # arms the timer
    assert bd.tick(25 * 60 - 1) is None  # not yet
    assert bd.tick(25 * 60)["posted"] == "Still on track?"
    assert bd.tick(25 * 60 + 5) is None  # next one is a full interval later
    assert bd.tick(50 * 60)["mode"] == "voice"


def test_glance_names_window_and_falls_back(tmp_path):
    bd, _ = make(tmp_path, "invoice.xlsx")
    bd.update({"enabled": True, "mode": "glance", "interval_min": 1})
    bd.tick(0)
    assert bd.tick(60)["posted"] == "Still on invoice.xlsx?"
    bd.title_fn = lambda: (_ for _ in ()).throw(OSError("locked"))
    assert bd.tick(120)["posted"] == "Still on track?"


def test_voice_mode_never_reads_the_window(tmp_path):
    bd, _ = make(tmp_path)
    bd.title_fn = boom
    bd.update({"enabled": True, "mode": "voice", "interval_min": 1})
    bd.tick(0)
    assert bd.tick(60)["posted"] == "Still on track?"


def test_silent_buzzes_only_at_breaks_and_never_reads_window(tmp_path):
    bd, _ = make(tmp_path)
    bd.title_fn = boom
    bd.update({"enabled": True, "mode": "silent", "interval_min": 1, "break_every": 2})
    bd.tick(0)
    assert bd.tick(60) is None
    assert bd.tick(120)["posted"] == "Break time"


def test_answer_is_logged_on_next_check(tmp_path):
    bd, log = make(tmp_path)
    bd.update({"enabled": True, "mode": "voice", "interval_min": 1})
    bd.tick(0)
    bd.tick(60)
    aid = bd.buddy.alerts()["alerts"][0]["id"]
    bd.buddy.alert_outcome(aid, "ack")
    bd.tick(120)
    assert log == [("checkin", "Still on track? Answer: yes.")]
    bd.tick(180)
    assert log[-1][1].endswith("Answer: no answer.")


def test_mode_switch_restarts_and_persists(tmp_path):
    bd, _ = make(tmp_path)
    bd.update({"enabled": True, "mode": "voice", "interval_min": 1})
    bd.tick(0)
    data = bd.update({"mode": "silent"})
    assert data["mode"] == "silent" and data["interval_min"] == 1
    assert BodyDouble(Buddy(), tmp_path / "bd.json").settings()["mode"] == "silent"
    assert bd.tick(60) is None  # countdown restarted


def test_ledger_format(tmp_path):
    p = append_ledger("checkin", "hello", now=datetime(2026, 11, 16, 9, 5), directory=tmp_path)
    assert p.name == "2026-11-16.md" and p.read_text() == "## 09:05 checkin\nhello\n\n"
