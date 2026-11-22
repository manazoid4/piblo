"""Piblo Core capability endpoints over the universal registry.

Mounts GET /capabilities and POST /capabilities/{cap_id}/execute so PibloCraft,
the handheld, and other clients can discover and run Tier 0 deterministic
capabilities through one shared contract. Each execution publishes
capability.execution.* events on the registry event bus.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .buddy import AlertIn
from .pc import ActionResult, PCController
from .universal_registry import Capability, UniversalCapabilityRegistry

PC_ACTIONS = (
    "desktop",
    "play_pause",
    "mute",
    "volume_down",
    "volume_up",
    "previous_track",
    "next_track",
    "lock",
)


class ExecuteBody(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


def _pc_handler(action: str, pc: PCController) -> Callable:
    def handler(args: dict[str, Any], context: dict[str, Any]) -> dict:
        result: ActionResult = pc.perform(action)
        return {"action": result.action, "label": result.label, "provider": "pc-local"}

    return handler


def _status_handler(core) -> Callable:
    def handler(args: dict[str, Any], context: dict[str, Any]) -> dict:
        return core.status()

    return handler


def _reminder_handler(buddy) -> Callable:
    def handler(args: dict[str, Any], context: dict[str, Any]) -> dict:
        title = str(args.get("title", "")).strip()
        if not title:
            raise ValueError("reminder_title_required")
        delay = int(args.get("delay_seconds", 60))
        if delay <= 0 or delay > 30 * 86_400:
            raise ValueError("reminder_delay_out_of_range")
        buddy.post_alert(
            AlertIn(
                source="reminder",
                kind="reminder",
                title=title[:60],
                priority="high",
                actions=["ack", "snooze"],
                delay_seconds=delay,
            )
        )
        return {
            "title": title,
            "delay_seconds": delay,
            "reply": f"Reminder set: {title}",
            "provider": "deterministic-local",
        }

    return handler


def install_registry_routes(
    api: FastAPI,
    *,
    registry: UniversalCapabilityRegistry,
    pc_controller: PCController,
    core_service,
    buddy,
) -> None:
    # Tier 0 PC controls (Windows media / desktop / lock), reusing PCController.
    for action in PC_ACTIONS:
        registry.register(
            Capability(
                id=f"pc.{action}",
                name=action.replace("_", " ").title(),
                description=f"Windows {action.replace('_', ' ')} control.",
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                category="computer-control",
                handler=_pc_handler(action, pc_controller),
            )
        )

    # System information.
    registry.register(
        Capability(
            id="system.status",
            name="System status",
            description="Core host status (version, OS, local engine, projects...).",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            category="system-information",
            handler=_status_handler(core_service),
        )
    )

    # Personal reminders (mirrors the deterministic chat-turn path).
    registry.register(
        Capability(
            id="reminder.create",
            name="Create reminder",
            description="Post a timed reminder alert to the companion device.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "delay_seconds": {"type": "integer"},
                },
                "required": ["title"],
            },
            output_schema={"type": "object"},
            category="personal-organization",
            required_permissions=["notifications"],
            handler=_reminder_handler(buddy),
        )
    )

    @api.get("/capabilities")
    def list_capabilities():
        return {"ok": True, "capabilities": registry.list_all()}

    @api.post("/capabilities/{cap_id}/execute")
    async def execute_capability(cap_id: str, body: ExecuteBody):
        try:
            return await registry.execute(cap_id, body.args, body.context)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        except NotImplementedError as error:
            raise HTTPException(501, str(error)) from error
