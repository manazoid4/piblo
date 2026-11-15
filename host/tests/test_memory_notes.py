"""Local daily memory works across restarts without a model or device."""
import asyncio
from datetime import date

import pytest

from mazhost.config import Settings
from mazhost.live_brain import LiveBrain


def store_at(tmp_path, day):
    from mazhost.memory_notes import MemoryNotes
    return MemoryNotes(tmp_path, today=lambda: day)


def test_daily_notes_append_and_reopen_with_tags_and_links(tmp_path):
    yesterday = store_at(tmp_path, date(2026, 11, 10))
    old_path = yesterday.remember("Dentist checkup #health")
    current = store_at(tmp_path, date(2026, 11, 11))
    path = current.remember("Dentist appointment is at noon #health")
    current.remember("Bring the appointment letter")
    text = path.read_text("utf-8")
    assert path == tmp_path / "memory" / "2026-11-11.md"
    assert text.startswith("---\ndate: 2026-11-11\ntags: ")
    assert '"health"' in text and '"dentist"' in text
    assert "Dentist appointment is at noon" in text and "Bring the appointment letter" in text
    assert "[[2026-11-10]]" in text
    assert "[[2026-11-11]]" in old_path.read_text("utf-8")
    reopened = store_at(tmp_path, date(2026, 11, 11))
    hits = reopened.recall("like I said yesterday about dentist")
    assert len(hits) == 1 and hits[0]["date"] == "2026-11-10"
    assert "Dentist checkup" in hits[0]["text"]


@pytest.mark.parametrize("query,start,end", [
    ("yesterday", "2026-11-10", "2026-11-10"),
    ("last week", "2026-11-02", "2026-11-08"),
    ("on Monday", "2026-11-09", "2026-11-09"),
    ("today", "2026-11-11", "2026-11-11"),
])
def test_relative_dates_are_inclusive_calendar_ranges(tmp_path, query, start, end):
    assert store_at(tmp_path, date(2026, 11, 11)).resolve_relative(query) == (
        date.fromisoformat(start), date.fromisoformat(end))


def test_recall_filters_recent_dates_and_does_not_match_links(tmp_path):
    for day, text in [(1, "dentist old record"), (8, "Dentist checkup #health"),
                      (10, "Walk outdoors #health"), (11, "dentist today")]:
        store_at(tmp_path, date(2026, 11, day)).remember(text)
    notes = store_at(tmp_path, date(2026, 11, 11))
    assert [h["date"] for h in notes.recall("dentist")] == ["2026-11-11", "2026-11-08"]
    assert [h["date"] for h in notes.recall("dentist last week")] == ["2026-11-08"]
    assert notes.recall("dentist yesterday") == []
    assert notes.resolve_relative("dentist") is None
    assert notes.recall("no-such-topic") == []


def test_invalid_input_does_not_create_files(tmp_path):
    notes = store_at(tmp_path, date(2026, 11, 11))
    with pytest.raises(ValueError):
        notes.remember(" \n ")
    with pytest.raises(ValueError):
        notes.recall("dentist", days=0)
    assert not list(tmp_path.iterdir())


def test_live_brain_remembers_daily_notes_and_retrieves_yesterday(tmp_path):
    notes = store_at(tmp_path, date(2026, 11, 10))
    notes.remember("Dentist appointment at noon")
    brain = LiveBrain(Settings(_env_file=None, data_dir=str(tmp_path), live_memory_github=""))
    brain.memory_notes = store_at(tmp_path, date(2026, 11, 11))
    assert "remember" in asyncio.run(brain.command("remember that bring the dentist letter"))
    assert "bring the dentist letter" in (tmp_path / "memory" / "2026-11-11.md").read_text("utf-8")
    prompt = asyncio.run(brain.system_prompt("like I said yesterday about dentist"))
    assert "2026-11-10" in prompt and "Dentist appointment at noon" in prompt


def test_stream_passes_current_query_into_memory_context(tmp_path):
    brain = LiveBrain(Settings(_env_file=None, data_dir=str(tmp_path), live_memory_github=""))
    store_at(tmp_path, date(2026, 11, 10)).remember("Dentist appointment at noon")
    brain.memory_notes = store_at(tmp_path, date(2026, 11, 11))
    brain._write_json("live-brain.json", {"model": "lfm", "label": "local"})

    async def fake_sse(base, key, model, messages, first_s):
        assert "Dentist appointment at noon" in messages[0]["content"]
        assert "2026-11-10" in messages[0]["content"]
        yield "Your dentist appointment is at noon."

    brain._sse = fake_sse

    async def run():
        return "".join([piece async for piece in brain.stream("like I said yesterday about dentist")])

    assert asyncio.run(run()) == "Your dentist appointment is at noon."


def test_memory_http_uses_same_store_and_validates_input(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mazhost.memory_notes import build_memory_router
    app = FastAPI()
    notes = store_at(tmp_path, date(2026, 11, 11))
    app.include_router(build_memory_router(notes))
    client = TestClient(app)
    assert client.post("/memory", json={"text": "Dentist at noon #health"}).status_code == 200
    hits = client.get("/memory/recall", params={"q": "dentist"}).json()["notes"]
    assert hits[0]["date"] == "2026-11-11"
    assert "Dentist at noon" in hits[0]["text"]
    assert client.post("/memory", json={"text": " "}).status_code == 422
    assert client.get("/memory/recall", params={"days": 0}).status_code == 422


def test_editable_notes_preserve_custom_frontmatter_and_text_after_links(tmp_path):
    old = store_at(tmp_path, date(2026, 11, 10))
    path = old.remember("Dentist checkup #health")
    store_at(tmp_path, date(2026, 11, 11)).remember("Dentist letter #health")
    text = path.read_text("utf-8")
    text = text.replace("date: 2026-11-10", "date: 2026-11-10\ncategory: personal")
    path.write_text(text + "\nA handwritten update stays here.\n", "utf-8")
    old.remember("Bring an appointment letter")
    updated = path.read_text("utf-8")
    assert "category: personal" in updated
    assert "A handwritten update stays here." in updated


def test_failed_atomic_write_keeps_saved_note_and_removes_temporary_file(tmp_path, monkeypatch):
    import mazhost.memory_notes as memory_notes
    notes = store_at(tmp_path, date(2026, 11, 11))
    path = notes.remember("Dentist at noon")
    before = path.read_text("utf-8")

    def fail_replace(source, target):
        raise OSError("disk unavailable")

    monkeypatch.setattr(memory_notes.os, "replace", fail_replace)
    with pytest.raises(OSError):
        notes.remember("Another appointment")
    assert path.read_text("utf-8") == before
    assert not list(notes.dir.glob("*.tmp"))


def test_recall_ranks_keyword_matches_before_recency(tmp_path):
    store_at(tmp_path, date(2026, 11, 10)).remember("Dentist appointment at noon")
    store_at(tmp_path, date(2026, 11, 11)).remember("Dentist checkup")
    notes = store_at(tmp_path, date(2026, 11, 11))
    assert [hit["date"] for hit in notes.recall("dentist appointment")] == ["2026-11-10", "2026-11-11"]


def test_live_brain_selects_matching_entries_before_context_cap(tmp_path):
    notes = store_at(tmp_path, date(2026, 11, 11))
    notes.dir.mkdir(parents=True, exist_ok=True)
    path = notes.dir / "2026-11-11.md"
    path.write_text("---\ndate: 2026-11-11\ntags: []\n---\n\n- " + "unrelated " * 700 +
                    "\n- Dentist appointment is at noon\n", encoding="utf-8")
    brain = LiveBrain(Settings(_env_file=None, data_dir=str(tmp_path), live_memory_github=""))
    brain.memory_notes = notes

    prompt = asyncio.run(brain.system_prompt("dentist appointment"))

    assert "Dentist appointment is at noon" in prompt
    assert len(prompt.split("Relevant saved daily notes", 1)[1]) < 6000


def test_non_utf8_daily_note_is_skipped_without_breaking_voice_memory(tmp_path):
    notes = store_at(tmp_path, date(2026, 11, 11))
    bad = notes.dir / "2026-11-10.md"
    notes.dir.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\xff\xfe bad note")
    brain = LiveBrain(Settings(_env_file=None, data_dir=str(tmp_path), live_memory_github=""))
    brain.memory_notes = notes

    reply = asyncio.run(brain.command("remember that dentist appointment is at noon"))
    prompt = asyncio.run(brain.system_prompt("dentist"))

    assert reply == "Got it, I'll remember that."
    assert "dentist appointment is at noon" in prompt.lower()
    assert "2026-11-10" not in prompt
    assert bad.read_bytes() == b"\xff\xfe bad note"

    current = notes.dir / "2026-11-11.md"
    current.write_bytes(b"\xff\xfe current bad note")
    assert asyncio.run(brain.command("remember another idea")) == "Sorry, I couldn't save that."
    assert current.read_bytes() == b"\xff\xfe current bad note"


def test_secret_filter_allows_bus_pass_and_secret_santa():
    from mazhost import memory_notes as m
    assert not m.is_sensitive("pick up my bus pass")
    assert not m.is_sensitive("secret santa is friday")
    assert m.is_sensitive("my wifi password is hunter2")
