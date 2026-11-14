import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_flow(tmp_path, **overrides):
    from mazhost import flow_routes

    settings = SimpleNamespace(flow_cloud=False, flow_cleanup_llm=False, groq_api_key="",
                               groq_key="", stt_provider="parakeet", flow_dir=str(tmp_path))
    for key, value in overrides.items():
        setattr(settings, key, value)
    fake = SimpleNamespace(s=settings, dir=Path(tmp_path), words=["Piblo", "Cardputer"])
    return fake


def make_client(flow):
    from mazhost.flow_routes import build_flow_router

    app = FastAPI()
    app.include_router(build_flow_router(flow))
    return TestClient(app)


def test_settings_default_to_paste_and_auto(tmp_path):
    api = make_client(make_flow(tmp_path))
    body = api.get("/flow/settings").json()
    assert body["input_mode"] == "paste"
    assert body["cleanup"] == "auto"
    assert body["press_enter"] is False
    assert body["effective_cleanup"] == "rules"
    assert body["has_groq_key"] is False


def test_patch_settings_persists_and_applies_to_live_flow(tmp_path):
    flow = make_flow(tmp_path, groq_api_key="k")
    api = make_client(flow)
    body = api.patch("/flow/settings", json={"input_mode": "type", "cleanup": "groq",
                                             "press_enter": True}).json()
    assert body["input_mode"] == "type" and body["effective_cleanup"] == "groq"
    assert flow.s.flow_cloud is True and flow.s.flow_cleanup_llm is False
    stored = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert stored == {"input_mode": "type", "cleanup": "groq", "press_enter": True, "stt": "auto"}

    flow2 = make_flow(tmp_path, groq_api_key="k")
    api2 = make_client(flow2)
    assert flow2.s.flow_cloud is True  # re-applied on router build, no Core restart needed
    assert api2.get("/flow/settings").json()["input_mode"] == "type"


def test_groq_without_key_falls_back_to_rules(tmp_path):
    flow = make_flow(tmp_path)
    api = make_client(flow)
    assert api.patch("/flow/settings", json={"cleanup": "groq"}).json()["effective_cleanup"] == "rules"


def test_cleanup_rules_wins_over_local_flag(tmp_path):
    flow = make_flow(tmp_path, flow_cleanup_llm=True)
    api = make_client(flow)
    api.patch("/flow/settings", json={"cleanup": "rules"})
    assert flow.s.flow_cleanup_llm is False and flow.s.flow_cloud is False


def test_dictionary_add_dedupe_and_remove(tmp_path):
    flow = make_flow(tmp_path)
    api = make_client(flow)
    assert api.post("/flow/dictionary", json={"word": "Kubernetes"}).status_code == 200
    api.post("/flow/dictionary", json={"word": "kubernetes"})  # case-insensitive duplicate
    words = api.get("/flow/dictionary").json()["words"]
    assert [w for w in words if w.lower() == "kubernetes"] == ["Kubernetes"]  # first spelling wins
    assert api.delete(f"/flow/dictionary/{'kubernetes'}").status_code == 200
    assert "kubernetes" not in api.get("/flow/dictionary").json()["words"]
    on_disk = (tmp_path / "dictionary.txt").read_text(encoding="utf-8")
    assert "kubernetes" not in on_disk
    assert flow.words == api.get("/flow/dictionary").json()["words"]


def test_dictionary_keeps_alias_lines_when_adding_a_word(tmp_path):
    """PR #130 stores `Term: alias, alias` lines; adding a word must not flatten them."""
    flow = make_flow(tmp_path)
    flow.lines = ["Rampage: ram page, ram payge", "Piblo"]
    api = make_client(flow)
    (tmp_path / "dictionary.txt").write_text("\n".join(flow.lines) + "\n", encoding="utf-8")
    body = api.post("/flow/dictionary", json={"word": "Kubernetes"}).json()
    assert body["words"] == ["Rampage", "Piblo", "Kubernetes"]
    assert body["aliased"] == 1
    assert body["entries"][0]["aliases"] == ["ram page", "ram payge"]
    on_disk = (tmp_path / "dictionary.txt").read_text(encoding="utf-8").splitlines()
    assert on_disk[0] == "Rampage: ram page, ram payge"
    assert flow.lines == on_disk
    assert flow.words == ["Rampage", "Piblo", "Kubernetes"]


def test_dictionary_alias_entry_merges_into_existing_term(tmp_path):
    flow = make_flow(tmp_path)
    flow.lines = ["Rampage: ram page"]
    api = make_client(flow)
    body = api.post("/flow/dictionary", json={"word": "rampage: ramp age, ram page"}).json()
    assert body["words"] == ["Rampage"]
    assert body["entries"] == [{"term": "Rampage", "aliases": ["ram page", "ramp age"]}]


def test_dictionary_removing_a_term_removes_its_aliases(tmp_path):
    flow = make_flow(tmp_path)
    flow.lines = ["Rampage: ram page", "Piblo"]
    api = make_client(flow)
    (tmp_path / "dictionary.txt").write_text("Rampage: ram page\nPiblo\n", encoding="utf-8")
    body = api.delete("/flow/dictionary/rampage").json()
    assert body["words"] == ["Piblo"]
    assert (tmp_path / "dictionary.txt").read_text(encoding="utf-8") == "Piblo\n"


def test_dictionary_rejects_bad_alias(tmp_path):
    api = make_client(make_flow(tmp_path))
    assert api.post("/flow/dictionary", json={"word": "Rampage: bad/alias"}).status_code == 400
    assert api.post("/flow/dictionary", json={"word": ": no term"}).status_code == 400


@pytest.mark.parametrize("word", ["", "   ", "a" * 65, "bad/word", "semi;colon", "quote\"d"])
def test_dictionary_rejects_bad_words(tmp_path, word):
    api = make_client(make_flow(tmp_path))
    assert api.post("/flow/dictionary", json={"word": word}).status_code in (400, 422)


def test_status_summarises_latency_and_errors(tmp_path):
    flow = make_flow(tmp_path)
    Path(tmp_path, "latency.log").write_text(
        "2026-11-14T10:00:00 dictate intent=paste stt=120 clean=3 total=130 via=rules stt_via=parakeet chars=42\n"
        "2026-11-14T10:01:00 dictate intent=note stt=200 clean=4 total=210 via=groq stt_via=parakeet chars=10\n"
        "garbage line without metrics\n",
        encoding="utf-8",
    )
    Path(tmp_path, "errors.log").write_text("2026-11-14T10:02:00 dictate failed ENOENT wav\n", encoding="utf-8")
    body = make_client(flow).get("/flow/status").json()
    assert body["count"] == 2
    assert body["recent"][0]["intent"] == "note" and body["recent"][0]["total_ms"] == 210
    assert body["avg_total_ms"] == 170
    assert body["p95_total_ms"] == 210
    assert body["errors"] and "ENOENT" in body["errors"][-1]


def test_status_on_empty_dir_is_safe(tmp_path):
    body = make_client(make_flow(tmp_path)).get("/flow/status").json()
    assert body == {"recent": [], "count": 0, "avg_total_ms": 0, "p95_total_ms": 0,
                    "errors": [], "effective_cleanup": "rules"}


def test_insert_text_uses_configured_mode(tmp_path, monkeypatch):
    from mazhost import flow, flow_routes

    flow_ = make_flow(tmp_path)
    calls = {}
    monkeypatch.setattr(flow_routes, "type_text", lambda text: calls.setdefault("typed", text))
    monkeypatch.setattr(flow_routes, "_tap", lambda *vks: calls.setdefault("taps", vks))
    monkeypatch.setattr(flow, "paste", lambda text, enter=False: calls.setdefault("pasted", (text, enter)))
    assert flow_routes.insert_text("hello", False, flow_) == "paste"
    assert calls["pasted"] == ("hello", False)

    write_settings = flow_routes.write_settings
    write_settings(flow_, {"input_mode": "type", **{k: v for k, v in flow_routes.DEFAULTS.items() if k != "input_mode"}})
    assert flow_routes.insert_text("hello", True, flow_) == "type"
    assert calls["typed"] == "hello"
    assert calls["taps"] == (0x0D,)  # Linux CI has no ctypes.windll: _tap must be stubbed


def test_input_struct_size_matches_64bit_windows():
    """A KEYBDINPUT-only union made INPUT 32 bytes; SendInput needs 40 on x64."""
    import ctypes
    from mazhost import flow_routes

    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(flow_routes.INPUT) == expected
    assert ctypes.sizeof(flow_routes.KEYBDINPUT) == (24 if expected == 40 else 16)


def test_apply_settings_auto_restores_config_values(tmp_path):
    from mazhost import flow_routes

    flow = make_flow(tmp_path, flow_cloud=True, flow_cleanup_llm=True, stt_provider="whisper")
    flow_routes.apply_settings(flow, {"cleanup": "rules", "stt": "parakeet"})
    assert (flow.s.flow_cloud, flow.s.flow_cleanup_llm, flow.s.stt_provider) == (False, False, "parakeet")
    flow_routes.apply_settings(flow, {"cleanup": "auto", "stt": "auto"})
    assert (flow.s.flow_cloud, flow.s.flow_cleanup_llm, flow.s.stt_provider) == (True, True, "whisper")


def test_dictionary_write_is_atomic_and_leaves_no_temp(tmp_path):
    from mazhost import flow_routes

    flow = make_flow(tmp_path)
    flow.lines = ["Piblo"]
    flow_routes.save_lines(flow, ["Piblo", "Rampage: ram page"])
    assert (tmp_path / "dictionary.txt").read_text(encoding="utf-8") == "Piblo\nRampage: ram page\n"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".dictionary-")] == []


def test_dictionary_is_capped(tmp_path):
    from mazhost import flow_routes

    flow = make_flow(tmp_path)
    flow.lines = [f"Word{i}" for i in range(flow_routes.DICTIONARY_MAX)]
    api = make_client(flow)
    assert api.post("/flow/dictionary", json={"word": "OneTooMany"}).status_code == 400