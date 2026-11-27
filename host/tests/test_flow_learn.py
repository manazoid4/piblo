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
    ("I brought a large coat today", "I brought a larger coat today"),
    ("use a small screen today", "use a smaller screen today"),
    ("use account number 12345678 now", "use account number 12345679 now"),
    ("we are organizing the files today", "we are organising the files today"),
    ("pick another color for the logo", "pick another colour for the logo"),
    ("please run the test from the terminal", "please run Pytest from the terminal"),
])
def test_style_edits_are_ignored(L, old, new):
    assert L.observe(old, new) == []
    assert L.learned() == []


def test_common_words_are_never_learned(L):
    for _ in range(3):
        L.observe("more than that is fine", "more then that is fine")
    assert L.learned() == []


def test_promotion_threshold_two_sightings_for_plain_words(L):
    a, b = "the scrib is over there", "the scribe is over there"
    L.observe(a, b)
    assert active(L) == set() and L.learned()[0]["status"] == "candidate"
    L.observe(a, b)
    assert active(L) == {("scrib", "scribe")}


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
    assert apply_dictionary("versal", L.rules("code")) == "Versel"
    assert apply_dictionary("versal", ["Versel: versal", "Vercel: versal"]) == "Vercel"
    assert {e["app"] for e in L.learned()} == {"", "code"}


def test_dictionary_leaves_unmapped_unicode_lookalikes_intact():
    assert apply_dictionary("\u0131stanbul and \u017fend", ["Istanbul", "send"]) == "\u0131stanbul and \u017fend"


def test_conflict_does_not_immediately_replace_active_rule(L):
    L.observe("deploy to versal now", "deploy to Vercel now", app="chrome")
    assert active(L)
    L.observe("deploy to versal now", "deploy to Versel now", app="chrome")
    assert not active(L)
    L.observe("deploy to versal now", "deploy to Vercel now", app="chrome")
    assert not active(L)
    L.observe("deploy to versal now", "deploy to Vercel now", app="chrome")
    assert active(L) == {("versal", "Vercel")}


def test_additional_sightings_persist(tmp_path):
    learner = Learner(tmp_path)
    for _ in range(3):
        learner.observe("deploy to versal now", "deploy to Vercel now")
    assert Learner(tmp_path).learned()[0]["seen"] == 3



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
    pasted = "open it in the codec app now"
    samples = iter([(f"note: {pasted} ok", "notepad", (7, 1)),
                    ("note: open it in the Codex app now ok", "notepad", (7, 1))])
    watch_paste(L.observe, pasted, ("note:  ok", "notepad", (7, 1)), 0, lambda: next(samples)).join(2)
    assert ("codec", "Codex") in active(L)


@pytest.mark.parametrize("after", [None,
    ("note: open it in the Codex app now ok", "notepad", (7, 2)),
    ("note: open it in the Codex app now ok", "other", (7, 1)),
    ("different prefix: open it in the Codex app now ok", "notepad", (7, 1)),
])
def test_watch_paste_ignores_password_wrong_field_and_other_edits(L, after):
    pasted = "open it in the codec app now"
    samples = iter([(f"note: {pasted} ok", "notepad", (7, 1)), after])
    watch_paste(L.observe, pasted, ("note:  ok", "notepad", (7, 1)), 0, lambda: next(samples)).join(2)
    assert L.learned() == []


def test_watch_paste_skips_if_focus_changed_before_initial_readback(L):
    pasted = "open it in the codec app now"
    samples = iter([(pasted, "notepad", (7, 2)),
                    ("open it in the Codex app now", "notepad", (7, 2))])
    watch_paste(L.observe, pasted, ("", "notepad", (7, 1)), 0, lambda: next(samples)).join(2)
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
    disabled = api.post("/flow/learned/observe", json={"pasted": "open codec now please", "final": "open Codex now please"})
    assert disabled.json() == {"events": []}


def test_after_paste_respects_learn_setting(tmp_path):
    from mazhost.flow import Flow

    s = SimpleNamespace(flow_dir=str(tmp_path), groq_api_key="", flow_learn=False)
    f = Flow(s, None)
    f.learn_delay = 0
    f.after_paste("open it in the codec app now", "pasted", ("", "notepad", (7, 1)))
    time.sleep(0.1)
    assert f.learner.learned() == []


def test_batch_push_to_talk_submits_learning_to_local_core(monkeypatch):
    import httpx
    import nodflow
    from mazhost import flow_learn

    observations = []

    def request(req):
        if req.url.path == "/dictate":
            return httpx.Response(200, json={"text": "open codec now please", "intent": "paste", "ms": {}, "learn": True})
        if req.url.path == "/flow/settings":
            return httpx.Response(200, json={"learn": True})
        observations.append(json.loads(req.content))
        return httpx.Response(200)

    def watch(observe, pasted, target, *, enabled):
        assert enabled()
        observe(pasted, "open Codex now please", target[1])

    monkeypatch.setattr(flow_learn, "read_focused", lambda: ("", "notepad", (7, 1)))
    monkeypatch.setattr(flow_learn, "watch_paste", watch)
    monkeypatch.setattr(nodflow, "log", lambda _: None)
    for url in ("http://127.0.0.1:8787", "http://other-pc"):
        with httpx.Client(base_url=url, transport=httpx.MockTransport(request)) as client:
            nodflow.send(client, b"audio", time.perf_counter(), paste_fn=lambda text, enter: "pasted")
    assert observations == [{"pasted": "open codec now please", "final": "open Codex now please", "app": "notepad"}]
