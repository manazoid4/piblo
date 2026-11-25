"""Piblo command router: Tier 0 deterministic + Tier 1 small-model capability selection.

Route a free-text request to a Piblo capability id + structured arguments.

Tier 0 — deterministic pattern matching (commands.parse_command): fast, no model,
for frequent direct commands (media, volume, desktop, lock, reminders).
Tier 1 — small local model (qwen3.5:4b via ollama /v1/chat/completions) classifies
intent and selects ONE registered capability id; returns structured args + confidence.
Tier 2/3 escalation — returns tier="unclear" so the caller can ask for clarification
or hand to a more capable model/cloud, only when user permissions allow.

Designed so the router itself never performs an action: it only selects a
capability. The capability implementation (registered in UniversalCapabilityRegistry)
does the actual work and publishes capability.execution.* events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


from . import private_mode
from .commands import parse_command

SYSTEM_PROMPT = (
    "You are Piblo's command router. Read the user's request and pick at most ONE "
    "capability id from the list below, then extract its arguments. Reply with ONLY "
    "valid JSON: {\"intent\": short string, \"capability\": capability id or null, "
    "\"args\": object of arguments, \"confidence\": 0.0-1.0}. "
    "If no capability fits or you are unsure, set \"capability\": null and a low "
    "confidence. Do not invent capabilities not on the list.\n\n"
    "Registered capabilities:\n"
)


@dataclass
class RouteResult:
    tier: str  # "tier-0" | "tier-1" | "unclear"
    capability: str | None
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reasoning: str = ""


class CommandRouter:
    def __init__(
        self,
        *,
        ollama_url: str,
        model: str,
        client: Any = None,
        capabilities: list[dict[str, Any]] | None = None,
        registry=None,
    ) -> None:
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self.client = client or private_mode.client(timeout=30)
        self.capabilities = list(capabilities or [])
        self.registry = registry

    # ---------------------------------------------------------------- public
    def route(self, text: str) -> RouteResult:
        command = parse_command(text)
        if command:
            if command["type"] == "pc.action":
                return RouteResult(
                    "tier-0", f"pc.{command['action']}", {}, 1.0,
                    "deterministic pc control")
            if command["type"] == "reminder.create":
                return RouteResult(
                    "tier-0", "reminder.create",
                    {"title": command["title"], "delay_seconds": command["delay_seconds"]},
                    1.0, "deterministic reminder")
        return self._tier1(text)

    # ---------------------------------------------------------------- tier 1
    def _tier1(self, text: str) -> RouteResult:
        if self.registry is not None:
            caps_list = self.registry.list_all()
        else:
            caps_list = self.capabilities
        caps = "\n".join(
            f"- {c['id']}: {c['description']}" for c in caps_list
        ) or "(none registered)"
        system = SYSTEM_PROMPT + caps
        try:
            response = self.client.post(
                f"{self.ollama_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0,
                    "stream": False,
                },
                timeout=30,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except Exception:
            return RouteResult("unclear", None, {}, 0.0, "local model unavailable")

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return RouteResult("unclear", None, {}, 0.0, "model did not return JSON")

        capability = data.get("capability")
        if not capability:
            return RouteResult(
                "unclear", None, data.get("args", {}),
                float(data.get("confidence", 0.0)), data.get("intent", "model declined"))
        return RouteResult(
            "tier-1", capability, data.get("args", {}),
            float(data.get("confidence", 0.5)), data.get("intent", ""))
