"""Two-step voice requests: parsing, approval rules, labels and the runner."""
import threading
import time
from datetime import datetime, timezone

import pytest

from mazhost.compound import describe, needs_approval, parse_compound
from mazhost.compound_run import CompoundRunner
from mazhost.promises import propose

NOW = datetime(2027, 1, 4, 14, 0, tzinfo=timezone.utc)

PROMISE_EXAMPLES = [
    "save the spreadsheet I'm working on and remind me to finish it tonight",
    "start a 45 minute focus session and remind me to call Sam at 5pm",
    "save my document, then remind me tomorrow to email Ann",
    "mute my pc and remind me this evening to stretch",
    "save it and remind me to ring Bob on friday",
]


def test_save_then_promise():
    steps = parse_compound("Piblo, please save the spreadsheet I'm working on and remind me to finish it tonight.")
    assert steps == [
        {"type": "pc.save", "what": "spreadsheet"},
        {"type": "promise.create", "task": "finish it", "when": "tonight", "note": "finish it tonight",
         "needs_subject": True},
    ]


def test_focus_then_promise_with_clock_time():
    focus, promise = parse_compound("start a 45 minute focus session and remind me to call Sam at 5pm")
    assert focus == {"type": "focus.start", "minutes": 45}
    assert promise["task"] == "call Sam" and promise["when"] == "today at 5pm" and not promise["needs_subject"]


def test_pc_action_then_reminder():
    steps = parse_compound("mute my pc and remind me in 20 minutes to stretch")
    assert steps == [{"type": "pc.action", "action": "mute"},
                     {"type": "reminder.create", "delay_seconds": 1200, "title": "stretch"}]


@pytest.mark.parametrize("text", [
    "mute my pc", "remind me to call Sam tonight", "save the spreadsheet",
    "save money and remind me tonight", "save me a seat and remind me tomorrow",
    "start a 500 minute focus session and remind me tonight to rest",
])
def test_not_compound(text):
    assert parse_compound(text) is None


def test_and_inside_a_task_is_not_split():
    # Neither half of "... milk" / "eggs tomorrow" is a step, so the whole thing is refused (documented).
    assert parse_compound("remind me to buy milk and eggs tomorrow") is None


@pytest.mark.parametrize("text", PROMISE_EXAMPLES)
def test_every_promise_note_is_proposable(text):
    for step in parse_compound(text):
        if step["type"] == "promise.create":
            assert propose(step["note"], NOW)["candidates"]


def test_needs_approval():
    assert needs_approval([{"type": "pc.save", "what": "file"}])
    assert needs_approval([{"type": "pc.action", "action": "lock"}])
    assert not needs_approval([{"type": "pc.action", "action": "mute"}, {"type": "focus.start", "minutes": 25}])
    assert not needs_approval([{"type": "promise.create", "task": "x", "when": "today", "note": "x today",
                                "needs_subject": False}])


def test_describe_fits_the_screen():
    save = {"type": "pc.save", "what": "spreadsheet"}
    promise = {"type": "promise.create", "task": "finish it", "when": "tonight", "note": "", "needs_subject": True}
    assert describe(save, "Budget.ods") == "Save Budget.ods"
    assert describe(promise, "Budget.ods") == "Tonight: finish Budget"
    assert describe({"type": "focus.start", "minutes": 25}) == "Focus 25 min"
    assert describe({"type": "pc.action", "action": "mute"}) == "Mute PC"
    long_ = dict(promise, task="renegotiate the whole supplier contract")
    for step in (save, promise, long_, {"type": "reminder.create", "delay_seconds": 600, "title": "x" * 80}):
        assert len(describe(step, "A very long spreadsheet name.xlsx")) <= 28


# ---- runner with fakes ----
class FakeBuddy:
    """state() blocks until the test answers, like the real approval queue."""
    def __init__(self):
        self.answer, self.requests, self.alerts = threading.Event(), [], []
        self.decision = "allow"

    def request(self, tool, summary, **kw):
        self.requests.append((tool, summary))
        return {"id": "a1"}

    def state(self, rid, wait=0):
        self.answer.wait(5)
        return {"id": rid, "decision": self.decision}

    def post_alert(self, alert):
        self.alerts.append(alert)


class Rec:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def start(self, minutes):
        self.calls.append(("focus", minutes))

    def confirm(self, note, due):
        self.calls.append(("confirm", note))
        return {"ok": True}

    def save(self, hwnd):
        self.calls.append(("save", hwnd))
        if self.fail:
            raise RuntimeError("window changed; not saved")


def runner(decision="allow", fail=False):
    rec = Rec(fail)
    buddy = FakeBuddy()
    buddy.decision = decision
    r = CompoundRunner(buddy, rec, rec, None,
                       window=lambda: {"hwnd": 7, "title": "Budget.ods - OpenOffice Calc"}, save=rec.save)
    return r, rec


def settle(run, states=("done", "failed", "declined", "expired")):
    end = time.time() + 5
    while run["state"] not in states and time.time() < end:
        time.sleep(0.01)


def test_save_and_promise_waits_for_yes_then_runs():
    r, rec = runner()
    out = r.start("x", parse_compound("save the spreadsheet I'm working on and remind me to finish it tonight"))
    assert r.buddy.requests and rec.calls == [] and out["run"]["state"] == "waiting_for_yes"
    r.buddy.answer.set()
    settle(out["run"])
    assert rec.calls == [("save", 7), ("confirm", "finish Budget tonight")]
    assert out["run"]["state"] == "done" and r.buddy.alerts


def test_deny_runs_nothing():
    r, rec = runner("deny")
    out = r.start("x", parse_compound("save it and remind me tonight to rest"))
    r.buddy.answer.set()
    settle(out["run"])
    assert rec.calls == [] and out["run"]["state"] == "declined"


def test_failed_save_stops_before_the_promise():
    r, rec = runner(fail=True)
    out = r.start("x", parse_compound("save it and remind me tonight to rest"))
    r.buddy.answer.set()
    settle(out["run"])
    assert rec.calls == [("save", 7)] and out["run"]["state"] == "failed"


def test_focus_and_promise_run_without_approval():
    r, rec = runner()
    out = r.start("x", parse_compound("start a 25 minute focus session and remind me tonight to rest"))
    assert not r.buddy.requests
    assert rec.calls == [("focus", 25), ("confirm", "rest tonight")] and out["run"]["state"] == "done"


def test_ambiguous_voice_date_is_never_silently_saved(monkeypatch):
    # The runner must fail closed if a proposal ever has two readings (e.g. "next Friday"),
    # even though today's WHEN grammar only produces single-reading dates.
    from mazhost import compound_run
    real_propose = compound_run.propose
    monkeypatch.setattr(compound_run, "propose", lambda note: {
        **real_propose(note, NOW), "ambiguous": True,
        "candidates": [{"due": "2027-01-08T17:00", "label": "Fri 8 Jan, 5pm"},
                       {"due": "2027-01-15T17:00", "label": "Fri 15 Jan, 5pm"}]})
    r, rec = runner()
    out = r.start("x", parse_compound("save the spreadsheet and remind me tonight to rest"))
    r.buddy.answer.set()
    settle(out["run"])
    assert rec.calls == [("save", 7)]                     # stopped before the promise
    assert out["run"]["state"] == "failed"
    assert r.buddy.alerts and r.buddy.alerts[0].title.startswith("Stopped:")
    assert "two readings" in r.buddy.alerts[0].body


def test_voice_approval_expiry_runs_nothing():
    r, rec = runner()
    r.buddy.state = lambda rid, wait=0: {}                # the 90 s wait ran out with no YES
    out = r.start("x", parse_compound("save it and remind me tonight to rest"))
    settle(out["run"])
    assert rec.calls == [] and out["run"]["state"] == "expired"
    assert not r.buddy.alerts
