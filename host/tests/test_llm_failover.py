from __future__ import annotations

import json

import httpx
import pytest

from mazhost.config import Settings
from mazhost.errors import ErrorCode, RouteError
from mazhost.llm import Models


def settings(**values):
    return Settings(
        token="test-token-that-is-not-default",
        ollama_model="primary:test",
        ollama_backup_model="backup:test",
        _env_file=None,
        **values,
    )


def test_auto_policy_orders_primary_then_backup():
    models = Models(settings(local_model_policy="auto"))
    assert models._local_models() == ["primary:test", "backup:test"]


def test_backup_policy_can_force_low_vram_model():
    models = Models(settings(local_model_policy="backup"))
    assert models._local_models() == ["backup:test"]


def test_interactive_profiles_keep_models_warm_and_bound_output():
    smart = Models(settings(ai_profile="smart"))
    fast = Models(settings(ai_profile="fast"))
    save = Models(settings(ai_profile="save"))
    messages = [{"role": "user", "content": "give me the next action"}]

    assert smart._keep_alive() == "15m"
    assert fast._keep_alive() == "60m"
    assert save._keep_alive() == 0
    assert smart._options("primary:test", messages)["num_predict"] == 160


def test_local_route_falls_back_without_cloud(monkeypatch):
    models = Models(settings(local_model_policy="auto"))
    calls: list[str] = []

    def local_one(model, _messages):
        calls.append(model)
        if model == "primary:test":
            raise RuntimeError("gpu_busy")
        return "backup ready", f"local:{model}"

    monkeypatch.setattr(models, "_local_one", local_one)
    monkeypatch.setattr(
        models,
        "_cloud",
        lambda _messages: (_ for _ in ()).throw(AssertionError("cloud used")),
    )

    text, provider = models.chat([{"role": "user", "content": "hi"}], "local")
    assert text == "backup ready"
    assert provider == "local:backup:test"
    assert calls == ["primary:test", "backup:test"]


def _unavailable(*_args, **_kwargs):
    raise RouteError(ErrorCode.UPSTREAM_UNAVAILABLE, "test", retryable=True)


def test_auto_prefers_mazlatest_when_healthy(monkeypatch):
    models = Models(settings(mazlatest_key="m"))
    monkeypatch.setattr(models, "_mazlatest", lambda _messages, timeout=None: ("maz ok", "mazlatest:MazLatest"))
    monkeypatch.setattr(models, "_cloud", _unavailable)
    monkeypatch.setattr(models, "_local_stage", lambda *a, **k: (_ for _ in ()).throw(AssertionError("local used")))

    assert models.chat([{"role": "user", "content": "hi"}], "auto") == ("maz ok", "mazlatest:MazLatest")
    assert models._last_route["active"] == "mazlatest"
    assert models._last_route["fallback"] is False


def test_auto_falls_back_to_cloud_when_mazlatest_down(monkeypatch):
    models = Models(settings(mazlatest_key="m", cloud_key="c"))
    monkeypatch.setattr(models, "_mazlatest", _unavailable)
    monkeypatch.setattr(models, "_cloud", lambda _messages, timeout=None: ("cloud ok", "cloud"))
    monkeypatch.setattr(models, "_local_stage", lambda *a, **k: (_ for _ in ()).throw(AssertionError("local used")))

    assert models.chat([{"role": "user", "content": "hi"}], "auto") == ("cloud ok", "cloud")
    assert models._last_route["active"] == "cloud"
    assert models._last_route["fallback"] is True
    assert models._last_route["fallback_reason"] == ErrorCode.UPSTREAM_UNAVAILABLE.value


def test_auto_falls_back_to_local_fast_when_mazlatest_and_cloud_down(monkeypatch):
    models = Models(settings(local_model_policy="auto"))
    calls: list[str] = []

    def local_stage(stage, _messages, timeout=None):
        calls.append(stage)
        if stage == "local_fast":
            return "local fast ok", "local:primary:test"
        raise RouteError(ErrorCode.LOCAL_UNAVAILABLE, stage, retryable=False)

    monkeypatch.setattr(models, "_mazlatest", _unavailable)
    monkeypatch.setattr(models, "_cloud", _unavailable)
    monkeypatch.setattr(models, "_local_stage", local_stage)

    assert models.chat([{"role": "user", "content": "hi"}], "auto") == ("local fast ok", "local:primary:test")
    assert models._last_route["active"] == "local_fast"
    assert calls == ["local_fast"]


def test_auto_returns_explicit_degraded_when_every_stage_fails(monkeypatch):
    models = Models(settings())
    monkeypatch.setattr(models, "_mazlatest", _unavailable)
    monkeypatch.setattr(models, "_cloud", _unavailable)
    monkeypatch.setattr(
        models,
        "_local_stage",
        lambda stage, *a, **k: (_ for _ in ()).throw(RouteError(ErrorCode.LOCAL_UNAVAILABLE, stage, retryable=False)),
    )

    with pytest.raises(RouteError) as raised:
        models.chat([{"role": "user", "content": "hi"}], "auto")
    assert raised.value.route == "degraded"
    assert raised.value.requested_route == "auto"
    assert models._last_route["active"] == "degraded"
    assert models._last_route["ok"] is False


def test_auto_skips_cloud_when_it_shares_mazlatest_failure_domain(monkeypatch):
    # CLOUD pointed at the exact same 9router loopback + key as MAZLATEST:
    # a MAZLATEST outage takes CLOUD down with it. AUTO must not waste a
    # second network round-trip proving that live.
    models = Models(
        settings(
            local_model_policy="auto",
            cloud_url="http://localhost:20128/v1",
            cloud_key="",
            cloud_model="MazLatest",
            mazlatest_url="http://localhost:20128/v1",
            mazlatest_key="",
        )
    )
    monkeypatch.setattr(models, "_mazlatest", _unavailable)
    monkeypatch.setattr(models, "_cloud", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cloud must be skipped")))
    monkeypatch.setattr(
        models,
        "_local_stage",
        lambda stage, _messages, timeout=None: ("local fast ok", "local:primary:test")
        if stage == "local_fast"
        else (_ for _ in ()).throw(RouteError(ErrorCode.LOCAL_UNAVAILABLE, stage, retryable=False)),
    )

    assert models.chat([{"role": "user", "content": "hi"}], "auto") == ("local fast ok", "local:primary:test")
    assert models._last_route["active"] == "local_fast"
    assert "cloud" not in models._effective_auto_chain()


def test_auto_still_tries_cloud_when_genuinely_independent(monkeypatch):
    models = Models(
        settings(
            cloud_url="https://openrouter.ai/api/v1",
            cloud_key="real-separate-key",
            mazlatest_url="http://localhost:20128/v1",
            mazlatest_key="",
        )
    )
    assert "cloud" in models._effective_auto_chain()

    monkeypatch.setattr(models, "_mazlatest", _unavailable)
    monkeypatch.setattr(models, "_cloud", lambda _messages, timeout=None: ("cloud ok", "cloud"))
    monkeypatch.setattr(models, "_local_stage", lambda *a, **k: (_ for _ in ()).throw(AssertionError("local used")))

    assert models.chat([{"role": "user", "content": "hi"}], "auto") == ("cloud ok", "cloud")
    assert models._last_route["active"] == "cloud"


def test_auto_chain_has_no_recursive_auto_stage():
    # AUTO_CHAIN must only name concrete routes. If "auto" ever appears here,
    # AUTO could call itself and loop forever instead of terminating.
    from mazhost.llm import AUTO_CHAIN

    assert "auto" not in AUTO_CHAIN
    assert set(AUTO_CHAIN) <= {"groq", "groq_alt", "groq_3", "openrouter", "mazlatest", "cloud", "local_fast", "local_smart"}


def test_llamacpp_engine_uses_its_own_model_chain():
    models = Models(
        settings(
            local_engine="llamacpp",
            llamacpp_model="qwen3-4b",
            llamacpp_backup_model="gemma3-270m",
        )
    )
    assert models._local_models() == ["qwen3-4b", "gemma3-270m"]


def test_llamacpp_backup_model_can_live_on_a_second_server():
    models = Models(
        settings(
            local_engine="llamacpp",
            llamacpp_url="http://127.0.0.1:8080",
            llamacpp_model="qwen3-4b",
            llamacpp_backup_url="http://127.0.0.1:8081",
            llamacpp_backup_model="gemma3-270m",
        )
    )
    assert models._llamacpp_url("qwen3-4b") == "http://127.0.0.1:8080"
    assert models._llamacpp_url("gemma3-270m") == "http://127.0.0.1:8081"


def test_llamacpp_turn_is_non_streaming_and_bounded():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "local ready"}}],
                "usage": {"prompt_tokens": 41, "completion_tokens": 7},
                "timings": {"prompt_ms": 120.0, "predicted_ms": 380.0},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    models = Models(
        settings(local_engine="llamacpp", llamacpp_model="qwen3-4b"),
        client=client,
    )

    text, provider = models.chat([{"role": "user", "content": "hi"}], "local")
    assert (text, provider) == ("local ready", "local:qwen3-4b")
    assert seen["stream"] is False
    assert seen["max_tokens"] == 160
    assert seen["chat_template_kwargs"] == {"enable_thinking": False}
    assert models._last_usage["engine"] == "llamacpp"
    assert models._last_usage["total_ms"] == 500


def test_llamacpp_thinking_only_reply_is_named_not_silently_empty():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "Okay, the user said hi"},
                    }
                ]
            },
        )

    models = Models(
        settings(local_engine="llamacpp", llamacpp_model="qwen3-4b"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    try:
        models._llamacpp_one("qwen3-4b", [{"role": "user", "content": "hi"}])
    except RuntimeError as error:
        assert str(error) == "local_model_spent_budget_thinking"
    else:
        raise AssertionError("empty content must not be returned as an answer")


def test_llamacpp_status_reports_engine_and_health():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)

    models = Models(
        settings(local_engine="llamacpp", llamacpp_model="qwen3-4b"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    status = models.status()
    assert status["local"] is True
    assert status["local_engine"] == "llamacpp"
    assert status["local_model_installed"] is True
    assert status["backup_model_installed"] is False


def test_cloud_request_explicitly_disables_streaming_for_9router_compatibility():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "cloud ready"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    models = Models(
        settings(
            cloud_key="test-cloud-key",
            cloud_url="http://9router.test/v1",
            cloud_model="cloud:test",
        ),
        client=client,
    )

    assert models.chat([{"role": "user", "content": "hi"}], "cloud") == ("cloud ready", "cloud")
    assert seen["stream"] is False


def test_mazlatest_uses_explicit_loopback_route_and_exact_model_without_credentials():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://localhost:20128/v1/chat/completions"
        assert "authorization" not in request.headers
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "MazLatest ready"}}]},
        )

    models = Models(
        settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert models.chat([{"role": "user", "content": "hi"}], "mazlatest") == (
        "MazLatest ready",
        "mazlatest:MazLatest",
    )
    assert seen["model"] == "MazLatest"
    assert seen["stream"] is False
    assert seen["max_tokens"] == 160


def test_explicit_mazlatest_failure_is_loud_and_never_falls_back(monkeypatch):
    models = Models(settings())
    monkeypatch.setattr(
        models,
        "_mazlatest",
        lambda _messages: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    monkeypatch.setattr(
        models,
        "_cloud",
        lambda _messages: (_ for _ in ()).throw(AssertionError("cloud fallback used")),
    )
    monkeypatch.setattr(
        models,
        "_local",
        lambda _messages: (_ for _ in ()).throw(AssertionError("local fallback used")),
    )

    with pytest.raises(RouteError) as raised:
        models.chat([{"role": "user", "content": "hi"}], "mazlatest")
    assert raised.value.code == ErrorCode.UPSTREAM_UNAVAILABLE


def test_warm_is_one_token_skips_without_engine_and_keeps_route_status(monkeypatch):
    m = Models(settings())
    m._last_route = {"requested": "auto", "active": "groq", "ok": True}
    seen = []
    monkeypatch.setattr(m, "status", lambda: {"local": False})
    monkeypatch.setattr(m, "_dispatch_one", lambda *a, **k: seen.append(a) or ("", "local"))
    m.warm([{"role": "user", "content": "hi"}])
    assert seen == [], "no local engine: warm-up must not run"

    from mazhost.llm import output_cap
    caps = []
    monkeypatch.setattr(m, "status", lambda: {"local": True})
    monkeypatch.setattr(m, "_dispatch_one", lambda *a, **k: caps.append(output_cap()) or ("", "local"))
    m.warm([{"role": "user", "content": "hi"}])
    assert caps == [1] and output_cap() == 160
    assert m._last_route["active"] == "groq"
