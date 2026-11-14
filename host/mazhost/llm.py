from __future__ import annotations

import logging
from contextvars import ContextVar
import time
from typing import Any, Literal

import httpx

from .config import Settings
from .brain import is_refusal
from .errors import ErrorCode, RouteError, normalize_upstream_error
from . import private_mode

Route = Literal["local", "local_fast", "local_smart", "auto", "cloud", "mazlatest", "groq", "groq_alt", "groq_3", "openrouter"]

# Cloud routes that private mode skips: only local engines are tried.
CLOUD_ROUTES = frozenset({"groq", "groq_alt", "groq_3", "mazlatest", "openrouter", "cloud"})

# Explicit AUTO fallback order. One attempt per stage, no in-stage retries —
# a stage that fails (auth, model-not-found, timeout, refused connection...)
# just means that stage is unusable right now, so AUTO moves on immediately.
# CLOUD is a real stage in this chain, but until it is pointed at a provider
# genuinely separate from the 9router endpoint MAZLATEST uses, it does not
# add independence — see diagnostics()["independent_fallback_configured"].
AUTO_CHAIN: tuple[Route, ...] = ("groq", "groq_alt", "groq_3", "mazlatest", "openrouter", "cloud", "local_fast", "local_smart")
# MAZ_BRAIN_ORDER=local_first: the local model first, then free cloud (Groq,
# OpenRouter free), then 9router, then paid CLOUD (only when configured and
# independent), then the optional second local model.
LOCAL_FIRST_CHAIN: tuple[Route, ...] = ("local_fast", "groq", "groq_alt", "groq_3", "openrouter", "mazlatest", "cloud", "local_smart")
# A loopback health check either answers in milliseconds or the server is down.
LOCAL_HEALTH_TIMEOUT_SECONDS = 0.25

log = logging.getLogger("uvicorn.error.brain")

# Per-stage ceiling while AUTO is hopping through the chain. A user who
# explicitly picks one route (mazlatest/cloud/local_fast/...) keeps the
# client's full 90s patience; AUTO trades patience for not making the Pocket
# wait through up to four stacked 90s hangs before it gives up.
AUTO_STAGE_TIMEOUT_SECONDS = 15.0
# Groq answers in under 1 s; past 3 s hop on, so the worst case stays near the 3 s budget.
GROQ_TIMEOUT_SECONDS = 3.0

# The handheld prompt explicitly asks for compact answers. Bounding generation
# prevents a verbose local model from making the user wait for text that cannot
# usefully fit on the Pocket screen. Both local engines honour the same bound.
MAX_OUTPUT_TOKENS = 160
# Spoken turns: ~60 tokens is one or two short sentences, and decode is the slow part on CPU.
VOICE_MAX_TOKENS = 60
_output_cap: ContextVar[int] = ContextVar("output_cap", default=MAX_OUTPUT_TOKENS)


def output_cap() -> int:
    return _output_cap.get()


class Models:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or private_mode.client(timeout=90, limits=httpx.Limits(keepalive_expiry=300))
        self._last_usage: dict[str, int | float | str] = {}
        self._last_route: dict[str, Any] = {}

    @staticmethod
    def _auth_headers(key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _local_models(self) -> list[str]:
        if self.settings.local_engine == "llamacpp":
            primary = self.settings.llamacpp_model.strip()
            backup = self.settings.llamacpp_backup_model.strip()
        else:
            primary = self.settings.ollama_model.strip()
            backup = self.settings.ollama_backup_model.strip()
        policy = self.settings.local_model_policy
        if policy == "primary":
            return [primary] if primary else []
        if policy == "backup":
            return [backup] if backup else []
        result: list[str] = []
        for model in (primary, backup):
            if model and model not in result:
                result.append(model)
        return result

    def _llamacpp_url(self, model: str) -> str:
        backup = self.settings.llamacpp_backup_model.strip()
        backup_url = self.settings.llamacpp_backup_url.strip()
        if backup and backup_url and model == backup:
            return backup_url.rstrip("/")
        return self.settings.llamacpp_url.rstrip("/")

    def _ollama_probe(self) -> tuple[bool, set[str]]:
        names: set[str] = set()
        try:
            response = self.client.get(f"{self.settings.ollama_url.rstrip('/')}/api/tags", timeout=2)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return False, names
        if not response.is_success:
            return False, names
        try:
            for item in response.json().get("models", []):
                name = str(item.get("name") or item.get("model") or "")
                if name:
                    names.add(name)
        except (KeyError, TypeError, ValueError):
            pass
        return True, names

    def _llamacpp_probe(self) -> tuple[bool, set[str]]:
        # llama-server holds exactly the model it was started with, so a healthy
        # server is the only "installed" signal that means anything here. The
        # configured names are labels the request carries, not selectors.
        online = False
        for url in {self._llamacpp_url(model) for model in self._local_models()}:
            try:
                response = self.client.get(f"{url}/health", timeout=2)
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                continue
            if response.is_success:
                online = True
        return online, set(self._local_models()) if online else set()

    def _local_stage_healthy(self, stage: str) -> bool:
        # Fast skip for a dead local server: one loopback GET with a 250 ms
        # ceiling instead of waiting out the generation timeout. llama-server
        # answers 503 on /health while the model is still loading, which is
        # also "not usable now".
        if self.settings.local_engine == "llamacpp":
            url = f"{self._llamacpp_url(self._local_stage_model(stage))}/health"
        else:
            url = f"{self.settings.ollama_url.rstrip('/')}/api/tags"
        try:
            return self.client.get(url, timeout=LOCAL_HEALTH_TIMEOUT_SECONDS).is_success
        except Exception:
            return False

    def status(self) -> dict:
        engine = self.settings.local_engine
        if engine == "llamacpp":
            local, names = self._llamacpp_probe()
            primary = self.settings.llamacpp_model
            backup = self.settings.llamacpp_backup_model
            endpoint = self.settings.llamacpp_url
        else:
            local, names = self._ollama_probe()
            primary = self.settings.ollama_model
            backup = self.settings.ollama_backup_model
            endpoint = self.settings.ollama_url
        return {
            "local": local,
            "local_engine": engine,
            "local_endpoint": endpoint,
            "local_policy": self.settings.local_model_policy,
            "local_model": primary,
            "local_model_installed": primary in names,
            "backup_model": backup,
            "backup_model_installed": bool(backup) and backup in names,
            "local_chain": self._local_models(),
            "ai_profile": self.settings.ai_profile,
            "keep_alive": self._keep_alive(),
            "last_usage": dict(self._last_usage),
            "last_route": dict(self._last_route),
            "cloud": bool(self.settings.cloud_url and self.settings.cloud_model),
            "cloud_credential_configured": bool(self.settings.cloud_key),
        }

    def _keep_alive(self) -> str | int:
        if self.settings.ai_profile == "save":
            return 0
        if self.settings.ai_profile == "fast":
            return "60m"
        # SMART now biases toward interactive latency. Five minutes caused a
        # cold model reload during ordinary gaps between Pocket conversations.
        return "15m"

    def _context_size(self, messages: list[dict[str, str]]) -> int:
        # Character count is intentionally cheap; tokenizing on the laptop just
        # to choose a context window would add more work than this decision is
        # worth. Ordinary Pocket turns stay at 4K; grounded/project-heavy turns
        # get headroom only when their payload actually needs it.
        chars = sum(len(str(message.get("content", ""))) for message in messages)
        if self.settings.ai_profile == "save":
            return 6144 if chars > 16_000 else 4096
        if chars > 20_000:
            return 8192
        if chars > 10_000:
            return 6144
        return 4096

    def _options(self, model: str, messages: list[dict[str, str]]) -> dict:
        options = {
            "temperature": 0.15,
            "top_p": 0.85,
            "repeat_penalty": 1.05,
            "num_ctx": self._context_size(messages),
            "num_predict": output_cap(),
        }
        if model == self.settings.ollama_backup_model:
            options.update({"temperature": 0.30, "top_p": 0.90, "top_k": 30})
        return options

    def _local_one(
        self, model: str, messages: list[dict[str, str]], timeout: float | None = None
    ) -> tuple[str, str]:
        if self.settings.local_engine == "llamacpp":
            return self._llamacpp_one(model, messages, timeout=timeout)
        return self._ollama_one(model, messages, timeout=timeout)

    def _llamacpp_one(
        self, model: str, messages: list[dict[str, str]], timeout: float | None = None
    ) -> tuple[str, str]:
        # llama-server speaks the OpenAI shape and accepts llama.cpp sampling
        # names alongside it. Context size is fixed by the server's -c flag, so
        # the adaptive num_ctx used for Ollama has nothing to set here.
        url = self._llamacpp_url(model)
        response = self.client.post(
            f"{url}/v1/chat/completions",
            **({} if timeout is None else {"timeout": timeout}),
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0.15,
                "top_p": 0.85,
                "repeat_penalty": 1.05,
                "max_tokens": output_cap(),
                # Reusing the cached prefix is what keeps a second Pocket turn
                # from re-evaluating the whole grounded prompt.
                "cache_prompt": True,
                # Reasoning models put their thinking in `reasoning_content`
                # and leave `content` empty until they finish. Against a
                # 160-token bound that means the whole budget is spent thinking
                # and the Pocket gets nothing back, so thinking is switched off
                # at the template. Templates without the flag ignore it.
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        response.raise_for_status()
        payload = response.json()
        message = payload["choices"][0]["message"]
        text = (message.get("content") or "").strip()
        if not text:
            # A reasoning model that ran out of budget mid-thought answers with
            # an empty content and a full reasoning_content. Name that case so
            # the chain error says why rather than just "unavailable".
            if (message.get("reasoning_content") or "").strip():
                raise RuntimeError("local_model_spent_budget_thinking")
            raise RuntimeError("local_model_empty_reply")
        usage = payload.get("usage") or {}
        timings = payload.get("timings") or {}
        self._last_usage = {
            "provider": f"local:{model}",
            "engine": "llamacpp",
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_ms": round(
                float(timings.get("prompt_ms") or 0) + float(timings.get("predicted_ms") or 0)
            ),
        }
        return text, f"local:{model}"

    def _ollama_one(
        self, model: str, messages: list[dict[str, str]], timeout: float | None = None
    ) -> tuple[str, str]:
        options = self._options(model, messages)
        response = self.client.post(
            f"{self.settings.ollama_url.rstrip('/')}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "keep_alive": self._keep_alive(),
                "options": options,
                # Hybrid-reasoning models (qwen3.5, lfm2.5) otherwise burn most
                # of num_predict on a "Thinking Process:" preamble before ever
                # emitting the answer the Pocket screen needs.
                "think": False,
            },
            **({} if timeout is None else {"timeout": timeout}),
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["message"]["content"].strip()
        if not text:
            raise RuntimeError("local_model_empty_reply")
        self._last_usage = {
            "provider": f"local:{model}",
            "engine": "ollama",
            "num_ctx": int(options["num_ctx"]),
            "load_ms": round(int(payload.get("load_duration") or 0) / 1_000_000),
            "prompt_tokens": int(payload.get("prompt_eval_count") or 0),
            "output_tokens": int(payload.get("eval_count") or 0),
            "total_ms": round(int(payload.get("total_duration") or 0) / 1_000_000),
        }
        return text, f"local:{model}"

    def _local(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        last_error: RouteError | None = None
        for model in self._local_models():
            try:
                return self._local_one(model, messages)
            except Exception as error:
                last_error = normalize_upstream_error(
                    error, route="local", model=model, local=True
                )
        if last_error is not None:
            raise RouteError(
                ErrorCode.LOCAL_UNAVAILABLE,
                "local",
                upstream_status=last_error.upstream_status,
                retryable=True,
                model=last_error.model,
            ) from last_error
        raise RouteError(ErrorCode.LOCAL_UNAVAILABLE, "local", retryable=True)

    def _local_stage_model(self, stage: str) -> str:
        if self.settings.local_engine == "llamacpp":
            primary = self.settings.llamacpp_model.strip()
            backup = self.settings.llamacpp_backup_model.strip()
        else:
            primary = self.settings.ollama_model.strip()
            backup = self.settings.ollama_backup_model.strip()
        return primary if stage == "local_fast" else backup

    def _local_stage(
        self, stage: str, messages: list[dict[str, str]], timeout: float | None = None
    ) -> tuple[str, str]:
        # Single attempt against exactly one named model. No in-stage retry —
        # AUTO's chain is what provides resilience, not looping here.
        model = self._local_stage_model(stage)
        if not model:
            raise RouteError(ErrorCode.LOCAL_UNAVAILABLE, stage, retryable=False)
        try:
            return self._local_one(model, messages, timeout=timeout)
        except Exception as error:
            raise normalize_upstream_error(error, route=stage, model=model, local=True) from error

    def _local_fast(self, messages: list[dict[str, str]], timeout: float | None = None) -> tuple[str, str]:
        return self._local_stage("local_fast", messages, timeout=timeout)

    def _local_smart(self, messages: list[dict[str, str]], timeout: float | None = None) -> tuple[str, str]:
        return self._local_stage("local_smart", messages, timeout=timeout)

    def _openai_completion(
        self,
        *,
        route: str,
        url: str,
        key: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        timeout: float | None = None,
        extra: dict | None = None,
    ) -> tuple[str, str]:
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
            **(extra or {}),
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        try:
            response = self.client.post(
                f"{url.rstrip('/')}/chat/completions",
                headers=self._auth_headers(key),
                json=body,
                **({} if timeout is None else {"timeout": timeout}),
            )
            response.raise_for_status()
            payload = response.json()
            choices = payload["choices"]
            if not isinstance(choices, list) or not choices:
                raise ValueError("missing choices")
            message = choices[0]["message"]
            text = message["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty content")
            resolved_model = str(payload.get("model") or model)
            return text.strip(), resolved_model
        except Exception as error:
            raise normalize_upstream_error(error, route=route, model=model) from error

    def _cloud(self, messages: list[dict[str, str]], timeout: float | None = None) -> tuple[str, str]:
        if not self.settings.cloud_key:
            raise RouteError(
                ErrorCode.AUTH_FAILED, "cloud", retryable=False, model=self.settings.cloud_model
            )
        text, resolved_model = self._openai_completion(
            route="cloud",
            url=self.settings.cloud_url,
            key=self.settings.cloud_key,
            model=self.settings.cloud_model,
            messages=messages,
            timeout=timeout,
        )
        self._last_usage = {"provider": "cloud", "model": resolved_model}
        return text, "cloud"

    def _groq_model(self, stage: str) -> str:
        models = [m.strip() for m in self.settings.groq_models.split(",") if m.strip()]
        index = {"groq": 0, "groq_alt": 1, "groq_3": 2}[stage]
        return models[index] if len(models) > index else ""

    def _groq(self, stage: str, messages: list[dict[str, str]], timeout: float | None = None) -> tuple[str, str]:
        model = self._groq_model(stage)
        if not self.settings.groq_key or not model:
            raise RouteError(ErrorCode.AUTH_FAILED, stage, retryable=False, model=model or None)
        reasoning = "gpt-oss" in model  # reasoning model: keep thinking short and leave room to answer
        text, resolved = self._openai_completion(
            route=stage,
            url=self.settings.groq_url,
            key=self.settings.groq_key,
            model=model,
            messages=messages,
            max_tokens=400 if reasoning else output_cap(),
            timeout=GROQ_TIMEOUT_SECONDS if timeout is None else min(timeout, GROQ_TIMEOUT_SECONDS),
            extra={"reasoning_effort": "low"} if reasoning else None,
        )
        provider = f"groq:{resolved}"
        self._last_usage = {"provider": provider, "model": resolved}
        return text, provider

    def _openrouter(self, messages: list[dict[str, str]], timeout: float | None = None) -> tuple[str, str]:
        if not self.settings.openrouter_api_key:
            raise RouteError(ErrorCode.AUTH_FAILED, "openrouter", retryable=False)
        text, resolved = self._openai_completion(
            route="openrouter", url="https://openrouter.ai/api/v1", key=self.settings.openrouter_api_key,
            model=self.settings.openrouter_free_model, messages=messages, max_tokens=output_cap(),
            timeout=min(timeout or 5.0, 5.0),
        )
        return text, f"openrouter:{resolved}"

    def _mazlatest(self, messages: list[dict[str, str]], timeout: float | None = None) -> tuple[str, str]:
        text, resolved_model = self._openai_completion(
            route="mazlatest",
            url=self.settings.mazlatest_url,
            key=self.settings.mazlatest_key,
            model=self.settings.mazlatest_model,
            messages=messages,
            max_tokens=output_cap(),
            timeout=timeout,
        )
        provider = f"mazlatest:{self.settings.mazlatest_model}"
        self._last_usage = {"provider": provider, "model": resolved_model}
        return text, provider

    def _dispatch_one(self, stage: Route, messages: list[dict[str, str]]) -> tuple[str, str]:
        # A single, non-recursive attempt at exactly one named route. AUTO
        # calls this in a loop; it never calls itself or "auto" through here.
        if stage == "openrouter":
            return self._openrouter(messages)
        if stage.startswith("groq"):
            return self._groq(stage, messages)
        if stage == "mazlatest":
            return self._mazlatest(messages)
        if stage == "cloud":
            return self._cloud(messages)
        if stage == "local_fast":
            return self._local_fast(messages)
        if stage == "local_smart":
            return self._local_smart(messages)
        if stage == "local":
            return self._local(messages)
        raise ValueError(f"unknown route stage: {stage}")

    def warm(self, messages: list[dict[str, str]]) -> None:
        """Load the local model and prime its prompt cache with a 1-token completion.

        Skipped when no local engine is up; leaves last_route/last_usage untouched so
        diagnostics never show a warm-up as a user turn."""
        if not self.status()["local"]:
            return
        usage = dict(self._last_usage)
        token = _output_cap.set(1)
        try:
            self._dispatch_one("local", messages)
        finally:
            _output_cap.reset(token)
            self._last_usage = usage

    def _dispatch_one_bounded(self, stage: Route, messages: list[dict[str, str]]) -> tuple[str, str]:
        if self.settings.brain_order == "local_first" and stage in ("local_fast", "local_smart"):
            if not self._local_stage_healthy(stage):
                raise RouteError(ErrorCode.LOCAL_UNAVAILABLE, stage, retryable=True)
            return self._local_stage(stage, messages, timeout=self.settings.local_first_timeout_s)
        if stage == "openrouter":
            return self._openrouter(messages, timeout=AUTO_STAGE_TIMEOUT_SECONDS)
        if stage.startswith("groq"):
            return self._groq(stage, messages, timeout=AUTO_STAGE_TIMEOUT_SECONDS)
        if stage == "mazlatest":
            return self._mazlatest(messages, timeout=AUTO_STAGE_TIMEOUT_SECONDS)
        if stage == "cloud":
            return self._cloud(messages, timeout=AUTO_STAGE_TIMEOUT_SECONDS)
        if stage in ("local_fast", "local_smart"):
            return self._local_stage(stage, messages, timeout=AUTO_STAGE_TIMEOUT_SECONDS)
        raise ValueError(f"unknown AUTO stage: {stage}")

    def _cloud_is_independent_of_mazlatest(self) -> bool:
        # Config-only check, no network call. The endpoint is what defines
        # the failure domain: two routes hitting the same URL share the same
        # process, same network path, same outage — regardless of which key
        # each one presents. A different key at the *same* URL is still the
        # same 9router process going down together, so it is deliberately
        # NOT treated as independent; only a genuinely different endpoint is.
        return self.settings.cloud_url.rstrip("/") != self.settings.mazlatest_url.rstrip("/")

    def _local_smart_is_distinct(self) -> bool:
        # On llama.cpp a "backup" label pointing at the same server is the same
        # model; trying it again after local_fast failed only doubles the wait.
        backup = self._local_stage_model("local_smart")
        if not backup or backup == self._local_stage_model("local_fast"):
            return False
        if self.settings.local_engine == "llamacpp":
            return self._llamacpp_url(backup) != self.settings.llamacpp_url.rstrip("/")
        return True

    def _effective_auto_chain(self) -> tuple[Route, ...]:
        if private_mode.enabled():
            # Only local engines; every cloud stage is skipped.
            return tuple(stage for stage in ("local_fast", "local_smart") if self._local_stage_model(stage))
        if self.settings.brain_order == "local_first":
            chain = LOCAL_FIRST_CHAIN
            if not self._local_smart_is_distinct():
                chain = tuple(stage for stage in chain if stage != "local_smart")
            if not self.settings.cloud_key:  # paid CLOUD only when a key is configured
                chain = tuple(stage for stage in chain if stage != "cloud")
            chain = self._drop_unkeyed(chain)
            # CLOUD sharing 9router's endpoint is only redundant when the
            # MAZLATEST stage is actually in the chain; otherwise it is the one
            # cloud hop left after local.
            if "mazlatest" in chain and not self._cloud_is_independent_of_mazlatest():
                chain = tuple(stage for stage in chain if stage != "cloud")
            return chain
        chain = self._drop_unkeyed(AUTO_CHAIN)
        if self._cloud_is_independent_of_mazlatest():
            return chain
        return tuple(stage for stage in chain if stage != "cloud")

    def _drop_unkeyed(self, chain: tuple[Route, ...]) -> tuple[Route, ...]:
        if not self.settings.cloud_key:
            chain = tuple(stage for stage in chain if stage != "cloud")
        if not self.settings.groq_key:
            chain = tuple(stage for stage in chain if not stage.startswith("groq"))
        if not self.settings.mazlatest_key:  # keyless 9router stage only burns ~2 s before local
            chain = tuple(stage for stage in chain if stage != "mazlatest")
        if not self.settings.openrouter_api_key:
            chain = tuple(stage for stage in chain if stage != "openrouter")
        return chain

    def _auto(self, messages: list[dict[str, str]], started: float) -> tuple[str, str]:
        chain = self._effective_auto_chain()
        if private_mode.enabled() and not chain:
            raise RouteError(ErrorCode.PRIVATE_MODE, "auto", retryable=False, requested_route="auto")
        last_error: RouteError | None = None
        refused: tuple[str, tuple[str, str]] | None = None
        attempts: list[dict[str, str | int]] = []
        for stage in chain:
            stage_started = time.perf_counter()
            try:
                result = self._dispatch_one_bounded(stage, messages)
            except Exception as error:
                last_error = normalize_upstream_error(error, route=stage, local=stage.startswith("local"))
                attempts.append({"stage": stage, "error": last_error.code.value,
                                 "ms": round((time.perf_counter() - stage_started) * 1000)})
                continue
            attempts.append({"stage": stage, "ms": round((time.perf_counter() - stage_started) * 1000)})
            # Refusal guard: a "can't / no access" reply gets exactly one retry on the next stage.
            if refused is None and is_refusal(result[0]) and stage != chain[-1]:
                refused = (stage, result)
                continue
            fallback = stage != chain[0]
            self._last_route = {
                "requested": "auto",
                "active": stage,
                "ok": True,
                "fallback": fallback,
                "fallback_reason": last_error.code.value if fallback and last_error else None,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "order": self.settings.brain_order,
                "attempts": attempts,
            }
            self._log_route()
            return result
        if refused is not None:  # retry stages all failed: a refusal still beats an outage
            self._last_route = {
                "requested": "auto", "active": refused[0], "ok": True, "fallback": False,
                "refusal_retry_failed": True,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "order": self.settings.brain_order,
                "attempts": attempts,
            }
            self._log_route()
            return refused[1]
        # Every stage in the chain failed. Explicit DEGRADED, never an
        # unhandled 500 — the caller gets the last real failure as context.
        degraded_reason = last_error.code.value if last_error else "all_routes_unavailable"
        self._last_route = {
            "requested": "auto",
            "active": "degraded",
            "ok": False,
            "fallback": True,
            "fallback_reason": degraded_reason,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "order": self.settings.brain_order,
            "attempts": attempts,
        }
        self._log_route()
        raise RouteError(
            ErrorCode.ROUTE_UNAVAILABLE, "degraded", retryable=False, requested_route="auto"
        ) from last_error

    def _log_route(self) -> None:
        # One line per AUTO turn: which stage answered, total latency, and
        # every stage tried on the way (with its error code and time).
        route = self._last_route
        trail = " ".join(
            f"{a['stage']}:{a.get('error', 'ok')}:{a['ms']}ms" for a in route.get("attempts", [])
        )
        log.info("brain: order=%s active=%s ok=%s latency_ms=%s trail=[%s]",
                 route.get("order"), route.get("active"), route.get("ok"), route.get("latency_ms"), trail)

    def chat(self, messages: list[dict[str, str]], route: Route) -> tuple[str, str]:
        started = time.perf_counter()
        if private_mode.enabled() and route in CLOUD_ROUTES:
            raise RouteError(ErrorCode.PRIVATE_MODE, route, retryable=False, requested_route=route)
        if route == "auto":
            return self._auto(messages, started)
        try:
            result = self._dispatch_one(route, messages)
            self._last_route = {
                "requested": route,
                "active": route,
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
            return result
        except Exception as error:
            normalized = normalize_upstream_error(
                error,
                route=route,
                model=(
                    self.settings.mazlatest_model
                    if route == "mazlatest"
                    else self.settings.cloud_model if route == "cloud" else None
                ),
                local=route.startswith("local"),
            )
            normalized.requested_route = route
            self._last_route = {
                "requested": route,
                "active": route,
                "ok": False,
                "error": normalized.code.value,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
            raise normalized from error

    def _models_probe(self, url: str, key: str) -> dict:
        if private_mode.enabled() and not private_mode.is_local_url(url):
            return {"reachable": False, "authenticated": False, "auth_status": "unreachable", "models": []}
        endpoint = f"{url.rstrip('/')}/models"
        try:
            response = self.client.get(endpoint, headers=self._auth_headers(key), timeout=3)
        except httpx.TimeoutException:
            return {"reachable": False, "authenticated": False, "auth_status": "timeout", "models": []}
        except httpx.HTTPError:
            return {"reachable": False, "authenticated": False, "auth_status": "unreachable", "models": []}
        if response.status_code in (401, 403):
            return {"reachable": True, "authenticated": False, "auth_status": "rejected", "models": []}
        if response.status_code == 404:
            return {"reachable": True, "authenticated": False, "auth_status": "endpoint_missing", "models": []}
        if not response.is_success:
            return {"reachable": True, "authenticated": False, "auth_status": "upstream_error", "models": []}
        try:
            payload = response.json()
            items = payload.get("data", payload)
            models = [str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")]
        except (AttributeError, KeyError, TypeError, ValueError):
            return {"reachable": True, "authenticated": False, "auth_status": "invalid_response", "models": []}

        auth_status = "not_required"
        if key:
            try:
                invalid = self.client.get(
                    endpoint,
                    headers={"Authorization": "Bearer maz-diagnostics-invalid"},
                    timeout=3,
                )
                if invalid.status_code in (401, 403):
                    auth_status = "accepted"
            except httpx.HTTPError:
                pass
        return {
            "reachable": True,
            "authenticated": True,
            "auth_status": auth_status,
            "models": models,
        }

    def diagnostics(self, preferred_route: Route) -> dict:
        runtime = self.status()
        maz_probe = self._models_probe(self.settings.mazlatest_url, self.settings.mazlatest_key)
        if (
            self.settings.cloud_url.rstrip("/") == self.settings.mazlatest_url.rstrip("/")
            and self.settings.cloud_key == self.settings.mazlatest_key
        ):
            cloud_probe = maz_probe
        else:
            cloud_probe = self._models_probe(self.settings.cloud_url, self.settings.cloud_key)
        maz_model_available = self.settings.mazlatest_model in maz_probe["models"]
        cloud_model_available = self.settings.cloud_model in cloud_probe["models"]
        cloud_available = bool(cloud_probe["authenticated"] and cloud_model_available)
        local_available = bool(runtime["local"])
        local_fast_available = bool(local_available and runtime["local_model_installed"])
        local_smart_available = bool(local_available and runtime["backup_model_installed"])
        # CLOUD only counts as a genuinely independent failure domain from
        # MAZLATEST if it is a different endpoint or a different credential.
        # As shipped, an operator can leave CLOUD pointed at the same 9router
        # loopback MAZLATEST uses — diagnostics must say so plainly rather
        # than report AUTO as resilient when it silently is not.
        independent_cloud = bool(cloud_available and self._cloud_is_independent_of_mazlatest())
        mazlatest_available = bool(maz_probe["authenticated"] and maz_model_available)
        auto_available = mazlatest_available or independent_cloud or local_fast_available or local_smart_available
        return {
            "preferred_route": preferred_route,
            "active_route": self._last_route.get("active"),
            "last_route": dict(self._last_route),
            "9router": {
                "reachable": maz_probe["reachable"],
                "authenticated": maz_probe["authenticated"],
                "auth_status": maz_probe["auth_status"],
            },
            "mazlatest": {
                "configured_aliases": [self.settings.mazlatest_model],
                "model_available": maz_model_available,
                "available": mazlatest_available,
            },
            "cloud": {
                "model": self.settings.cloud_model,
                "model_available": cloud_model_available,
                "available": cloud_available,
                "independent_of_mazlatest": independent_cloud,
            },
            "local": {
                "engine": runtime["local_engine"],
                "endpoint_available": local_available,
                "model": runtime["local_model"],
                "model_available": runtime["local_model_installed"],
                "available": local_available,
            },
            "local_fast": {
                "model": runtime["local_model"],
                "available": local_fast_available,
            },
            "local_smart": {
                "model": runtime["backup_model"],
                "available": local_smart_available,
            },
            "auto": {
                "chain": list(self._effective_auto_chain()),
                "configured_chain": list(LOCAL_FIRST_CHAIN if self.settings.brain_order == "local_first" else AUTO_CHAIN),
                "order": self.settings.brain_order,
                "available": auto_available,
                "degraded": not mazlatest_available and not independent_cloud,
                "independent_fallback_configured": independent_cloud or local_fast_available,
            },
            # Small, display-ready shape any client (host UI, phone control
            # page, device firmware) can render without re-deriving route
            # logic: "AI: MAZLATEST / Status: Healthy / Fallback: Ready".
            "summary": self._diagnostics_summary(
                mazlatest_available=mazlatest_available,
                auto_available=auto_available,
                independent_fallback=independent_cloud or local_fast_available,
                local_fast_available=local_fast_available,
            ),
        }

    def _diagnostics_summary(
        self,
        *,
        mazlatest_available: bool,
        auto_available: bool,
        independent_fallback: bool,
        local_fast_available: bool,
    ) -> dict:
        active = self._last_route.get("active")
        label = {
            "mazlatest": "MAZLATEST",
            "cloud": "CLOUD",
            "local_fast": "LOCAL_FAST",
            "local_smart": "LOCAL_SMART",
            "local": "LOCAL",
            "degraded": "DEGRADED",
        }.get(active, "MAZLATEST" if active is None else str(active).upper())
        status = "Healthy" if mazlatest_available else ("Degraded" if auto_available else "Unavailable")
        summary = {
            "ai": label,
            "status": status,
            "fallback": "Ready" if independent_fallback else "Not configured",
            "local": "Ready" if local_fast_available else "Unavailable",
        }
        if self._last_route.get("fallback"):
            summary["reason"] = self._last_route.get("fallback_reason") or "primary_unavailable"
        return summary
