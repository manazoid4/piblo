from __future__ import annotations

import test_app
from mazhost import brain
from mazhost.llm import Models
from test_llm_failover import settings

H = {"Authorization": "Bearer test-token-that-is-not-default"}


def test_maths_exact():
    assert "36" in brain.maths_line("what is 15% of 240")
    assert "84" in brain.maths_line("what is 12 times 7")
    assert brain.maths_line("tell me a joke") == ""
    assert brain.maths_line("2 ** 99999999 + 1") == ""  # no runaway exponent


def test_refusal_detector():
    for bad in ("I don't have access to real-time data.", "As an AI, I can't say.", "I'm unable to check that."):
        assert brain.is_refusal(bad)
    for ok in ("It's 3:05 pm.", "Paris.", "Sure, tell me what you need."):
        assert not brain.is_refusal(ok)


def test_priorities_from_now_file(tmp_path):
    f = tmp_path / "NOW.md"
    f.write_text("## 1. Site\n- [x] done\n- [ ] Ship hero\n## Later\n- [ ] ignore me\n", encoding="utf-8")
    line = brain.priorities_line("what should I work on now", str(f))
    assert "Ship hero" in line and "ignore me" not in line and "done" not in line
    assert brain.priorities_line("tell me a joke", str(f)) == ""


def test_groq_stages_first_only_with_key():
    assert Models(settings(mazlatest_key="m"))._effective_auto_chain()[0] == "mazlatest"
    chain = Models(settings(groq_key="k", cloud_key="c"))._effective_auto_chain()
    assert chain[:3] == ("groq", "groq_alt", "groq_3")


def test_refusal_retries_once_on_next_stage(monkeypatch):
    m = Models(settings(groq_key="k"))
    monkeypatch.setattr(m, "_groq", lambda stage, msgs, timeout=None: (
        ("As an AI, I can't tell the time.", "groq:a") if stage == "groq" else ("It is 3 pm.", "groq:b")))
    assert m.chat([{"role": "user", "content": "time?"}], "auto") == ("It is 3 pm.", "groq:b")


def test_refusal_everywhere_still_returns_a_reply(monkeypatch):
    m = Models(settings(groq_key="k"))
    monkeypatch.setattr(m, "_groq", lambda s, msgs, timeout=None: ("I can't help.", "groq:a"))
    monkeypatch.setattr(m, "_mazlatest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(m, "_cloud", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(m, "_local_stage", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert m.chat([{"role": "user", "content": "hi"}], "auto")[0] == "I can't help."


def test_prompt_has_maths_priorities_and_local_route_stays_local(monkeypatch, tmp_path):
    f = tmp_path / "NOW.md"
    f.write_text("## 1. Site\n- [ ] Ship hero\n", encoding="utf-8")
    monkeypatch.setattr("mazhost.app.weather_line", lambda t: "\nLive weather, London UK: 18C\n" if "weather" in t else "")
    models = test_app.FakeModels()
    monkeypatch.setenv("MAZ_NOW_PATH", str(f))
    api = test_app.client(models=models)
    sid = api.post("/session/start", headers=H).json()["session_id"]

    def ask(text):
        api.post("/turn/text", headers=H, json={"text": text, "session_id": sid, "route": "local"})
        return models.last_messages[-1]["content"]

    # #128: a local-only turn makes no outbound weather lookup (private push-to-talk), so no weather line.
    assert "Live weather" not in ask("what is the weather today")
    assert "36" in ask("what is 15% of 240")
    assert "Ship hero" in ask("what should I work on now")
    assert models.last_route == "local"  # #128: explicit LOCAL never upgrades to the cloud chain
    assert "never say you lack access" in ask("hi")


def test_groq_429_hops_to_next_model_not_local(monkeypatch):
    m = Models(settings(groq_key="k"))
    seen = []

    def groq(stage, msgs, timeout=None):
        seen.append(stage)
        if stage != "groq_3":
            raise RuntimeError("429 rate limited")
        return "ok", "groq:c"

    monkeypatch.setattr(m, "_groq", groq)
    monkeypatch.setattr(m, "_local_stage", lambda *a, **k: (_ for _ in ()).throw(AssertionError("local used")))
    assert m.chat([{"role": "user", "content": "hi"}], "auto") == ("ok", "groq:c")
    assert seen == ["groq", "groq_alt", "groq_3"]
