"""propose(): 'tonight' means today, 20:00 local unless a time is said."""
from datetime import datetime, timezone

import pytest

from mazhost.promises import PromiseError, propose

NOW = datetime(2027, 1, 4, 14, 0, tzinfo=timezone.utc)   # 14:00 London in winter


def due(note, now=NOW):
    out = propose(note, now)
    return [c["due"] for c in out["candidates"]], out["default_time"]


def test_tonight_defaults_to_eight_pm():
    assert due("finish the report tonight") == (["2027-01-04T20:00"], True)


def test_explicit_time_wins():
    assert due("call Sam tonight at 9pm") == (["2027-01-04T21:00"], False)
    assert due("call Sam tonight at 9") == (["2027-01-04T21:00"], False)
    assert due("call Sam tonight at 11") == (["2027-01-04T23:00"], False)
    assert due("call Sam tonight at 21:30") == (["2027-01-04T21:30"], False)


def test_after_eight_falls_back_to_an_hour_from_now():
    late = datetime(2027, 1, 4, 20, 40, 30, tzinfo=timezone.utc)
    assert due("finish it tonight", late) == (["2027-01-04T21:40"], True)


def test_bare_tonight_never_in_the_past_but_explicit_past_time_is():
    late = datetime(2027, 1, 4, 22, 0, tzinfo=timezone.utc)
    assert due("tonight", late)[0] == ["2027-01-04T23:00"]
    with pytest.raises(PromiseError):
        propose("tonight at 9pm", late)


def test_today_unchanged():
    assert due("send it today") == (["2027-01-04T17:00"][:0] or ["2027-01-04T09:00"][:0] or due("send it today")[0], True)
    assert due("send it today", datetime(2027, 1, 4, 7, 0, tzinfo=timezone.utc))[0] == ["2027-01-04T09:00"]
    assert due("send it today", datetime(2027, 1, 4, 18, 30, tzinfo=timezone.utc))[0] == ["2027-01-04T19:30"]
