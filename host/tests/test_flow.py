from __future__ import annotations

import io
import json
import wave

import pytest
from fastapi.testclient import TestClient

from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.flow import parse_prefix, rule_cleanup, load_dictionary


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    return buf.getvalue()


@pytest.mark.parametrize("raw,want", [
    ("um so uh this is the the plan", "So this is the plan."),
    ("i want a fix, uh, for flowlens", "I want a fix, for flowlens."),
    ("Already fine.", "Already fine."),
])
def test_rule_cleanup(raw, want):
    assert rule_cleanup(raw) == want


@pytest.mark.parametrize("text,want", [
    ("Note: buy cables", ("note", "buy cables")),
    ("note, buy cables.", ("note", "buy cables.")),
    ("Remind me to call Sam in 20 minutes", ("remind", "call Sam in 20 minutes")),
    ("Claude, fix the failing test.", ("claude", "fix the failing test.")),
    ("notebook sizes are odd", ("paste", "notebook sizes are odd")),
    ("Claudette said hi", ("paste", "Claudette said hi")),
])
def test_parse_prefix(text, want):
    assert parse_prefix(text) == want


def test_dictionary_default(tmp_path):
    words = load_dictionary(tmp_path / "dict.txt")
    assert "MAZos" in words and "Groq" in words and (tmp_path / "dict.txt").exists()


class Stt:
    prompts: list = []
    def available(self): return True
    def transcribe(self, p): return "um so note uh buy the the cables for cardputer"
    def transcribe_local(self, p): return self.transcribe(p), "parakeet"
    def transcribe_prompted(self, path, prompt):
        Stt.prompts.append(prompt)
        return "um so note uh buy the the cables for cardputer"


class Models:
    def chat(self, messages, route):
        assert "cardputer" in messages[-1]["content"].lower()
        assert "MAZos" in messages[0]["content"]  # dictionary reaches cleanup
        return "Note: buy the cables for Cardputer.", "fake"


def test_dictate_endpoint(tmp_path):
    cfg = Settings(token="t" * 40, core_enabled=False, flow_dir=str(tmp_path), groq_api_key="",
                   flow_cleanup_llm=True, stt_provider="whisper", flow_cloud=True)
    client = TestClient(create_app(cfg, stt=Stt(), models=Models()))
    r = client.post("/dictate?target=text", content=_wav(),
                    headers={"Authorization": "Bearer " + "t" * 40, "Content-Type": "audio/wav"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["intent"] == "note" and j["text"] == "buy the cables for Cardputer."
    assert "Cardputer" in Stt.prompts[0] and "nod" in Stt.prompts[0]
    assert j["ms"]["total"] >= 0
    assert "buy the cables" in (tmp_path / "inbox.md").read_text()
    assert "dictate" in (tmp_path / "latency.log").read_text()


@pytest.mark.parametrize("mode,intent,press_enter,outcome", [
    ("paste", "claude", False, "clipboard"),
    ("type", "paste", True, "pasted"),
])
def test_dictate_pc_paste_keeps_focus_guard_and_settings(tmp_path, monkeypatch, mode, intent, press_enter, outcome):
    from mazhost import app as app_module
    from mazhost.flow import Flow

    (tmp_path / "settings.json").write_text(json.dumps({
        "input_mode": mode, "press_enter": press_enter, "cleanup": "auto", "stt": "auto",
    }), encoding="utf-8")
    monkeypatch.setattr(Flow, "dictate", lambda self, path: {"intent": intent, "text": "Hello."})
    monkeypatch.setattr(app_module, "flow_foreground", lambda: 42)
    seen = {}

    def fake_safe_paste(text, enter, armed, paste_fn):
        seen.update(text=text, enter=enter, armed=armed, paste_fn=paste_fn)
        if paste_fn:
            paste_fn(text, enter)
        return outcome

    typed = []
    monkeypatch.setattr(app_module, "flow_safe_paste", fake_safe_paste)
    monkeypatch.setattr(app_module, "insert_text", lambda text, enter, flow: typed.append((text, enter)))
    cfg = Settings(token="t" * 40, core_enabled=False, flow_dir=str(tmp_path), groq_api_key="",
                   flow_cleanup_llm=False, stt_provider="whisper", flow_cloud=False)
    client = TestClient(create_app(cfg, stt=Stt(), models=Models()))
    response = client.post("/dictate?target=pc-paste", content=_wav(),
                           headers={"Authorization": "Bearer " + "t" * 40, "Content-Type": "audio/wav"})

    assert response.status_code == 200, response.text
    assert response.json()["pc"] == response.json()["reply"] == outcome
    assert seen["text"] == "Hello." and seen["armed"] == 42
    assert seen["enter"] is (intent == "claude" or press_enter)
    if mode == "type":
        assert typed == [("Hello.", True)]
    else:
        assert seen["paste_fn"] is None and typed == []


def test_one_release_one_paste(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import nodflow

    class R:
        def raise_for_status(self): pass
        def json(self): return {"text": "hello", "intent": "claude", "ms": {}}

    class C:
        def post(self, *a, **k): return R()

    calls = []
    nodflow.send(C(), b"x", 0.0, paste_fn=lambda t, enter=False: calls.append((t, enter)))
    assert calls == [("hello", True)]


def test_cleanup_is_local_by_default(tmp_path):
    from mazhost.flow import Flow

    class Boom:
        def chat(self, *a, **k): raise AssertionError("network route called")

    f = Flow(Settings(token="t" * 40, flow_dir=str(tmp_path), groq_api_key=""), Stt(), Boom())
    assert f._cleanup("um hello there") == ("Hello there.", "rules")


def _stt(tmp_path, **kw):
    from mazhost.stt import SpeechToText
    return SpeechToText(Settings(token="t" * 40, flow_dir=str(tmp_path), **kw))


def test_groq_key_keeps_parakeet_dictation_local(tmp_path):
    from mazhost.flow import Flow

    class Spy(Stt):
        def transcribe_prompted(self, *a): raise AssertionError("dictation audio sent to Groq")

    class Boom:
        def chat(self, *a, **k): raise AssertionError("network route called")

    class NoNet:
        def post(self, *a, **k): raise AssertionError("text sent to Groq")

    f = Flow(Settings(token="t" * 40, flow_dir=str(tmp_path), groq_api_key="k"), Spy(), Boom(), client=NoNet())
    out = f.dictate(tmp_path / "a.wav")
    assert out["text"] and "stt_via=parakeet" in (tmp_path / "latency.log").read_text()


def test_failed_parakeet_load_not_retried_and_logs_once(tmp_path, monkeypatch, caplog):
    import builtins
    s = _stt(tmp_path)
    calls = []
    real = builtins.__import__

    def imp(name, *a, **k):
        if name == "onnx_asr":
            calls.append(1)
            raise ImportError("nope")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", imp)
    monkeypatch.setattr(s, "_transcribe_with", lambda *a: "whisper text")
    for _ in range(3):
        assert s.transcribe_local(tmp_path / "a.wav") == ("whisper text", "whisper")
    # request path never marks failure (no download attempted there); only warm() does
    assert calls and not s._onnx_failed
    monkeypatch.setattr(s, "_get_model", lambda *a: None)
    s.warm()
    n = len(calls)
    assert s._onnx_failed
    for _ in range(3):
        assert s.transcribe_local(tmp_path / "a.wav") == ("whisper text", "whisper")
    assert len(calls) == n
    assert sum("parakeet load failed" in r.message for r in caplog.records) == 1


def test_transcribe_local_engine_and_other_paths_stay_whisper(tmp_path, monkeypatch):
    s = _stt(tmp_path)

    class Fake:
        def recognize(self, p): return " Hello world. "

    s._onnx = Fake()
    assert s.transcribe_local(tmp_path / "a.wav") == ("Hello world.", "parakeet")
    monkeypatch.setattr(s, "_transcribe_with", lambda *a: "whisper text")
    assert s.transcribe(tmp_path / "a.wav") == "whisper text"  # turns/BrainDump/teach unchanged


def test_warm_downloads_once_request_path_does_not(tmp_path, monkeypatch):
    s = _stt(tmp_path)
    seen = []
    monkeypatch.setattr(s, "_get_model", lambda *a: None)

    def fake(download=False):
        seen.append(download)
        raise RuntimeError("x")

    monkeypatch.setattr(s, "_get_onnx", fake)
    s.warm()
    assert seen == [True]
    monkeypatch.setattr(s, "_transcribe_with", lambda *a: "w")
    s.transcribe_local(tmp_path / "a.wav")
    assert seen == [True, False]


def test_whisper_not_blocked_by_onnx_lock(tmp_path, monkeypatch):
    import threading
    s = _stt(tmp_path)
    import sys, types
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=lambda *a, **k: "m"))
    done = []
    with s._onnx_lock:
        t = threading.Thread(target=lambda: done.append(s._get_model("base.en", "cpu", "int8")))
        t.start(); t.join(5)
    assert done == ["m"]


def test_custom_model_refused_on_request_path(tmp_path, monkeypatch):
    s = _stt(tmp_path, stt_local_model="moonshine-base-en")
    monkeypatch.setattr(s, "_transcribe_with", lambda *a: "w")
    assert s.transcribe_local(tmp_path / "a.wav") == ("w", "whisper")
    assert not s._onnx_failed


def test_request_does_not_wait_for_warm_download(tmp_path, monkeypatch):
    import time
    s = _stt(tmp_path)
    monkeypatch.setattr(s, "_transcribe_with", lambda *a: "w")
    with s._onnx_lock:  # warm() is mid-download
        t0 = time.perf_counter()
        assert s.transcribe_local(tmp_path / "a.wav") == ("w", "whisper")
        assert time.perf_counter() - t0 < 1
    assert not s._onnx_failed


def test_warm_loads_whisper_first(tmp_path, monkeypatch):
    s = _stt(tmp_path)
    order = []
    monkeypatch.setattr(s, "_get_model", lambda *a: order.append("whisper"))
    monkeypatch.setattr(s, "_get_onnx", lambda download=False: order.append("parakeet"))
    s.warm()
    assert order == ["whisper", "parakeet"]


def test_safe_paste_pastes_into_armed_window():
    from mazhost.flow import safe_paste
    got = []
    out = safe_paste("hi.", False, 42, paste_fn=lambda t, enter=False: got.append((t, enter)),
                     fg_fn=lambda: 42, copy_fn=lambda t: got.append(("copy", t)), typable_fn=lambda h: True)
    assert out == "pasted" and got == [("hi.", False)]


def test_safe_paste_keeps_text_on_clipboard_when_focus_moved():
    from mazhost.flow import safe_paste
    got = []
    out = safe_paste("hi.", True, 42, paste_fn=lambda t, enter=False: got.append("PASTED"),
                     fg_fn=lambda: 99, copy_fn=lambda t: got.append(("copy", t)))
    assert out == "clipboard" and got == [("copy", "hi.")]


def test_safe_paste_never_types_when_locked():
    from mazhost.flow import safe_paste
    got = []
    out = safe_paste("hi.", False, 0, paste_fn=lambda t, enter=False: got.append("PASTED"),
                     fg_fn=lambda: 0, copy_fn=lambda t: got.append("copy"))
    assert out == "clipboard" and "PASTED" not in got


def test_dictionary_fixes_what_parakeet_hears():
    from mazhost.flow import apply_dictionary
    got = apply_dictionary("So this is a test of Pebloflow on the card Puter.", ["Piblo", "Cardputer"])
    assert got == "So this is a test of Piblo Flow on the Cardputer."


def test_dictionary_user_aliases_and_no_false_join():
    from mazhost.flow import apply_dictionary
    assert apply_dictionary("ask graw queue", ["Groq: graw queue"]) == "ask Groq"
    assert apply_dictionary("a fish dish", ["Fish"]) == "a Fish dish"
    # ordinary word pairs are never glued into a term (no automatic splitting)
    assert apply_dictionary("come out look at this", ["Outlook"]) == "come out look at this"


def test_safe_paste_admin_window_goes_to_clipboard():
    from mazhost.flow import safe_paste
    got = []
    out = safe_paste("hi.", False, 42, paste_fn=lambda t, enter=False: got.append("PASTED"),
                     fg_fn=lambda: 42, copy_fn=lambda t: got.append("copy"), typable_fn=lambda h: False)
    assert out == "clipboard" and got == ["copy"]
