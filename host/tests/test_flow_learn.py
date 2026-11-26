import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost.flow import apply_dictionary
from mazhost.flow_learn import Learner, find_span, looks_secret, watch_paste


@pytest.fixture
def L(tmp_path):
    return Learner(tmp_path)


def active(l):
    return {(e["misheard"], e["term"]) for e in l.learned() if e["status"] == "active"}


@pytest.mark.parametrize("old,new,low", [
    ("I deployed it on versal today", "I deployed it on Vercel today", "versal"),
    ("open it in the codec app now", "open it in the Codex app now", "codec"),
    ("ask grok about the plan please", "ask Groq about the plan please", "grok"),
])
def test_genuine_fix_is_learned_and_strong_match_promotes_at_once(L, old, new, low):
    ev = L.observe(old, new)
    assert any(e["event"] == "promoted" for e in ev)
    assert low in {m for m, _ in active(L)}


@pytest.mark.parametrize("old,new", [
    ("send it to the client now", "Send it to the client now"),            # case
    ("send it to the client now", "send it, to the client, now."),         # punctuation
    ("this is a big problem today", "this is a large problem today"),      # synonym
    ("send it to the client now", "please could you kindly forward the whole thing over to them tomorrow"),  # rewrite
    ("send it to the client now please", "send it to the client"),         # deletion only
    ("send it to the client", "send it to the new client"),                # insertion only
    ("meet me at the station", "meet at the station"),
])
def test_style_edits_are_ignored(L, old, new):
    assert L.observe(old, new) == []
    assert L.learned() == []


def test_common_words_are_never_learned(L):
    for _ in range(3):
        L.observe("more than that is fine", "more then that is fine")
    assert L.learned() == []


def test_promotion_threshold_two_sightings_for_plain_words(L):
    a, b = "the flange was broken today", "the flanged was broken today"
    L.observe(a, b)
    assert active(L) == set() and L.learned()[0]["status"] == "candidate"
    L.observe(a, b)
    assert active(L) == {("flange", "flanged")}


def test_conflict_blocks_promotion(L):
    L.observe("call mister smith now", "call mister Smyth now")
    L.observe("call mister smith now", "call mister Smythe now")
    L.observe("call mister smith now", "call mister Smyth now")
    assert active(L) == set()
    assert any(e["confidence"] < 1 for e in L.learned())


def test_app_specific_rules(L):
    L.observe("deploy to versal now", "deploy to Vercel now", app="chrome")
    L.observe("deploy to versal now", "deploy to Versel now", app="code")
    assert L.rules("chrome") == ["Vercel: versal"]
    assert L.rules("code")[-1] == "Versel: versal"  # app-specific wins over global
    assert {e["app"] for e in L.learned()} == {"", "code"}


def test_undo_is_reversible_and_not_relearned(L):
    L.observe("deploy to versal now", "deploy to Vercel now")
    rid = L.learned()[0]["id"]
    assert L.rules() == ["Vercel: versal"]
    assert L.undo(rid) and L.rules() == [] and L.learned()[0]["status"] == "undone"
    L.observe("deploy to versal now", "deploy to Vercel now")
    assert L.rules() == []
    assert not L.undo("nope")


def test_guard_demotes_rule_that_breaks_a_corpus_pair(L):
    L.observe("ask grok about it", "ask Groq about it")
    assert L.rules() == ["Groq: grok"]
    # "grok" really is the right word in this sentence, so the rule makes it worse.
    assert L.guard([("grok is the xai model", "grok is the xai model")]) == [L.learned()[0]["id"]]
    assert L.rules() == [] and L.learned()[0]["status"] == "rejected"


def test_guard_keeps_a_helpful_rule(L):
    L.observe("deploy to versal now", "deploy to Vercel now")
    assert L.guard([("deploy to versal now", "deploy to Vercel now"), ("hello there", "hello there")]) == []
    assert L.rules() == ["Vercel: versal"]


def test_persistence_across_instances(tmp_path):
    a = Learner(tmp_path)
    a.observe("deploy to versal now", "deploy to Vercel now")
    b = Learner(tmp_path)
    assert b.rules() == ["Vercel: versal"] and b.learned() == a.learned()


def test_no_full_text_stored(tmp_path):
    l = Learner(tmp_path)
    l.observe("my secret plan is to deploy to versal tomorrow morning", "my secret plan is to deploy to Vercel tomorrow morning")
    raw = (tmp_path / "learned.json").read_text(encoding="utf-8")
    assert "secret plan" not in raw and "tomorrow" not in raw
    assert set(json.loads(raw)["entries"][0]) >= {"term", "misheard", "seen"}


def test_secrets_are_skipped(L):
    key = "sk-abcdef0123456789abcdef0123456789"
    assert looks_secret(key) and looks_secret("token a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")
    assert L.observe(f"key {key} for versal", f"key {key} for Vercel") == []


def test_find_span_in_a_larger_field():
    field = "Dear team,\n\nI deployed it on Vercel today and it works.\n\nThanks, Maz"
    assert find_span("I deployed it on versal today and it works", field) == "I deployed it on Vercel today and it works"
    assert find_span("completely different words here", field) is None


def test_watch_paste_reads_back_and_learns(L):
    reader = lambda: ("note: open it in the Codex app now ok", False, "notepad")
    watch_paste(L, "open it in the codec app now", "notepad", 0, reader).join(2)
    assert ("codec", "Codex") in active(L)


def test_watch_paste_ignores_password_and_failed_readback(L):
    def boom():
        raise OSError

    watch_paste(L, "open it in the codec app now", "x", 0, lambda: ("open it in the Codex app now", True, "x")).join(2)
    watch_paste(L, "open it in the codec app now", "x", 0, lambda: None).join(2)
    watch_paste(L, "open it in the codec app now", "x", 0, boom).join(2)
    watch_paste(L, "open it in the codec app now", "x", 0, lambda: ("open it in the Codex app now", False, "other")).join(2)
    assert L.learned() == []


def test_rules_merge_into_dictionary_and_overhead_under_1ms(L):
    for i in range(500):
        L._e.append({"id": f"{i:08x}", "term": f"Term{i}", "misheard": f"misheard{i}", "app": "", "seen": 2, "conflicts": 0,
                     "strong": False, "apps": [], "status": "active", "created": 0})
    L._save()
    text = "we should talk to misheard250 and versal about the plan today, thanks"
    lines = ["Piblo"] + L.rules()
    assert len(lines) == 501
    assert apply_dictionary(text, lines) == "we should talk to Term250 and Vercel about the plan today, thanks"
    n, t0 = 200, time.perf_counter()
    for _ in range(n):
        apply_dictionary(text, lines)
    per_call_ms = (time.perf_counter() - t0) / n * 1000
    print(f"apply overhead with 500 rules: {per_call_ms:.3f} ms")
    assert per_call_ms < 1.0
    t0 = time.perf_counter()
    for _ in range(n):
        L.rules()
    assert (time.perf_counter() - t0) / n * 1000 < 0.1  # cached


def test_routes(tmp_path):
    from mazhost.flow_routes import build_flow_router

    flow = SimpleNamespace(s=SimpleNamespace(flow_cloud=False, flow_cleanup_llm=False, groq_api_key="", groq_key="",
                                             stt_provider="parakeet"), dir=Path(tmp_path), words=[])
    app = FastAPI()
    app.include_router(build_flow_router(flow))
    api = TestClient(app)
    ev = api.post("/flow/learned/observe", json={"pasted": "deploy to versal now", "final": "deploy to Vercel now", "app": "chrome"}).json()
    assert [e["event"] for e in ev["events"]] == ["candidate", "promoted"]
    rows = api.get("/flow/learned").json()["entries"]
    assert rows[0]["status"] == "active" and rows[0]["app"] == ""
    assert api.delete(f"/flow/learned/{rows[0]['id']}").json()["entries"][0]["status"] == "undone"
    assert api.delete("/flow/learned/zzz").status_code == 404
    assert api.patch("/flow/settings", json={"learn": False}).json()["learn"] is False
    assert flow.s.flow_learn is False


def test_after_paste_respects_learn_setting(tmp_path):
    from mazhost.flow import Flow

    s = SimpleNamespace(flow_dir=str(tmp_path), groq_api_key="", flow_learn=False)
    f = Flow(s, None)
    f.learn_delay = 0
    f.after_paste("open it in the codec app now", "pasted", "x")
    time.sleep(0.1)
    assert f.learner.learned() == []
