"""Piblo Universal Capability Registry & Event Bus

Provides a standardized event bus and capability registry contract bridging Piblo Core,
PibloCraft, physical devices, and host companions.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger("piblo.registry")

@dataclass
class Capability:
    id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    category: str
    required_permissions: list[str] = field(default_factory=list)
    handler: Callable[..., Any] | None = None
    version: str = "1.0.0"
    # "read": looks only. "act": changes something the user asked for (media,
    # reminders, opening a page). "sensitive": changes system state or data
    # (ending a process, deleting temp files, network resets). Sensitive
    # capabilities always need an approved request from the user's device.
    risk: str = "act"
    # True only for low-impact actions that an automatic pipeline (voice,
    # router, agent) may run without a fresh approval.
    autonomous_ok: bool = False

@dataclass
class Event:
    type: str
    source: str
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:12]}")

class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[Event], Coroutine[Any, Any, None] | Any]]] = {}
        self._history: list[Event] = []
        self._max_history = 500

    def subscribe(self, event_type: str, callback: Callable[[Event], Any]) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    async def publish(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        # Notify specific subscribers
        # Copy so wildcard subscribers are not appended to the stored list on every publish
        subs = [*self._subscribers.get(event.type, []), *self._subscribers.get("*", [])]

        for sub in subs:
            try:
                if inspect.iscoroutinefunction(sub):
                    await sub(event)
                else:
                    sub(event)
            except Exception as e:
                logger.error(f"Error handling event {event.type}: {e}")

    def get_history(self, limit: int = 50) -> list[Event]:
        return self._history[-limit:]

RISKS = ("read", "act", "sensitive")
# Origins that are not a person pressing a button for this exact action.
AUTOMATIC_ORIGINS = {"voice", "router", "pipeline", "agent", "assist"}
# Permissions every install has had since the registry shipped; keeping them
# granted by default means existing capabilities behave exactly as before.
# screen_read and pc_fix are on by default so "sort this out" works with no
# setup; every pc_fix capability is "sensitive" and still needs a device yes.
DEFAULT_GRANTED = frozenset({"notifications", "device_control", "find_devices", "screen_read", "pc_fix"})


class PermissionPolicy:
    """Which permissions the user has granted, plus the approval check.

    Grants are stored on disk in Core's control folder and changed only through
    Core's own authenticated routes, never from the execute body. A caller
    cannot grant itself a permission by putting it in `context`.
    """

    def __init__(self, path: str | Path | None = None,
                 approval_ok: Callable[[str, str, dict], bool] | None = None,
                 defaults: frozenset[str] = DEFAULT_GRANTED) -> None:
        self.path = Path(path) if path else None
        self.approval_ok = approval_ok
        self._lock = threading.Lock()
        self._granted = set(defaults)
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._granted = set(data.get("granted", [])) | set(defaults) - set(data.get("revoked", []))
            except (OSError, ValueError):
                logger.warning("permission file unreadable; using defaults")
        self._defaults = set(defaults)

    def granted(self) -> list[str]:
        with self._lock:
            return sorted(self._granted)

    def set(self, permission: str, on: bool) -> list[str]:
        permission = permission.strip()
        if not permission or len(permission) > 60:
            raise ValueError("permission_name_invalid")
        with self._lock:
            (self._granted.add if on else self._granted.discard)(permission)
            self._save_locked()
            return sorted(self._granted)

    def _save_locked(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        revoked = sorted(self._defaults - self._granted)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"granted": sorted(self._granted), "revoked": revoked}), encoding="utf-8")
        os.replace(tmp, self.path)

    def check(self, cap: Capability, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
        """None when allowed, else {reason, missing?, needs_approval?}."""
        with self._lock:
            missing = [p for p in cap.required_permissions if p not in self._granted]
        if missing:
            return {"reason": "permission_not_granted", "missing": missing}
        origin = str(context.get("origin", "user"))
        needs_approval = cap.risk == "sensitive" or (
            cap.risk == "act" and origin in AUTOMATIC_ORIGINS and not cap.autonomous_ok)
        if not needs_approval:
            return None
        approval_id = str(context.get("approval_id", ""))
        if approval_id and self.approval_ok and self.approval_ok(approval_id, cap.id, args):
            return None
        return {"reason": "approval_required", "needs_approval": True, "risk": cap.risk}


class UniversalCapabilityRegistry:
    def __init__(self, event_bus: EventBus | None = None, policy: PermissionPolicy | None = None) -> None:
        self.capabilities: dict[str, Capability] = {}
        self.event_bus = event_bus or EventBus()
        # No policy = the original open behaviour (unit tests, embedded use).
        self.policy = policy

    def register(self, cap: Capability) -> None:
        self.capabilities[cap.id] = cap
        logger.info(f"Registered capability: {cap.id} ({cap.category})")

    def get(self, cap_id: str) -> Capability | None:
        return self.capabilities.get(cap_id)

    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "category": c.category,
                "version": c.version,
                "required_permissions": c.required_permissions,
                "risk": c.risk,
                "autonomous_ok": c.autonomous_ok,
                "input_schema": c.input_schema,
                "output_schema": c.output_schema,
            }
            for c in self.capabilities.values()
        ]

    async def execute(self, cap_id: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        cap = self.get(cap_id)
        if not cap:
            raise ValueError(f"Capability not found: {cap_id}")
        if not cap.handler:
            raise NotImplementedError(f"Capability {cap_id} has no execution handler.")
        context = dict(context or {})
        if self.policy is not None:
            blocked = self.policy.check(cap, args, context)
            if blocked:
                await self.event_bus.publish(Event(
                    type="capability.execution.blocked",
                    source="registry",
                    payload={"cap_id": cap_id, **blocked},
                ))
                return {"success": False, "blocked": True, "error": blocked["reason"], **blocked,
                        "duration_s": 0.0}

        await self.event_bus.publish(Event(
            type="capability.execution.started",
            source="registry",
            payload={"cap_id": cap_id, "args": args}
        ))

        start_time = time.time()
        try:
            if inspect.iscoroutinefunction(cap.handler):
                result = await cap.handler(args, context=context)
            else:
                result = cap.handler(args, context=context)

            duration = time.time() - start_time
            await self.event_bus.publish(Event(
                type="capability.execution.completed",
                source="registry",
                payload={"cap_id": cap_id, "duration_s": duration, "success": True}
            ))
            return {"success": True, "result": result, "duration_s": duration}
        except Exception as e:
            duration = time.time() - start_time
            await self.event_bus.publish(Event(
                type="capability.execution.failed",
                source="registry",
                payload={"cap_id": cap_id, "error": str(e), "duration_s": duration, "success": False}
            ))
            return {"success": False, "error": str(e), "duration_s": duration}

# Global registry and event bus singleton instance
global_event_bus = EventBus()
global_registry = UniversalCapabilityRegistry(global_event_bus)
