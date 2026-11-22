"""Piblo Universal Capability Registry & Event Bus

Provides a standardized event bus and capability registry contract bridging Piblo Core,
PibloCraft, physical devices, and host companions.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import dataclass, field
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

class UniversalCapabilityRegistry:
    def __init__(self, event_bus: EventBus | None = None) -> None:
        self.capabilities: dict[str, Capability] = {}
        self.event_bus = event_bus or EventBus()

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

        await self.event_bus.publish(Event(
            type="capability.execution.started",
            source="registry",
            payload={"cap_id": cap_id, "args": args}
        ))

        start_time = time.time()
        try:
            if inspect.iscoroutinefunction(cap.handler):
                result = await cap.handler(args, context=context or {})
            else:
                result = cap.handler(args, context=context or {})

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
