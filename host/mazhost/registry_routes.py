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
from .pairing import DevicePairingStore
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


def _capture_handler(perception) -> Callable:
    def handler(args: dict[str, Any], context: dict[str, Any]) -> dict:
        snap = perception.capture_screen()
        return {"path": snap.path, "size": list(snap.size),
                "captured_at": snap.captured_at, "source": snap.source,
                "provider": "screen-local"}

    return handler


def _ocr_handler(perception) -> Callable:
    def handler(args: dict[str, Any], context: dict[str, Any]) -> dict:
        image_path = args.get("image_path")
        if not image_path:
            raise ValueError("ocr_image_path_required")
        return perception.ocr_text(image_path)

    return handler


def _device_status_handler(monitor) -> Callable:
    def handler(args: dict[str, Any], context: dict[str, Any]) -> dict:
        return monitor.status()

    return handler


def _device_monitor_handler(monitor, action: str) -> Callable:
    def handler(args: dict[str, Any], context: dict[str, Any]) -> dict:
        if action == "start":
            return monitor.start()
        return monitor.stop()

    return handler


def _device_pairing_handlers(store: DevicePairingStore) -> dict[str, Callable]:
    def pending(args: dict[str, Any], context: dict[str, Any]) -> dict:
        return {"requests": store.pending()}

    def approve(args: dict[str, Any], context: dict[str, Any]) -> dict:
        request_id = str(args.get("request_id", "")).strip()
        code = args.get("code")
        if not request_id:
            raise ValueError("device_pairing_request_id_required")
        ok = store.decide(request_id, True, code)
        if not ok:
            raise ValueError("device_pairing_not_found_or_expired")
        return {"ok": True, "request_id": request_id, "state": "approved"}

    def deny(args: dict[str, Any], context: dict[str, Any]) -> dict:
        request_id = str(args.get("request_id", "")).strip()
        if not request_id:
            raise ValueError("device_pairing_request_id_required")
        ok = store.decide(request_id, False)
        if not ok:
            raise ValueError("device_pairing_not_found_or_expired")
        return {"ok": True, "request_id": request_id, "state": "denied"}

    return {"device.pairing.pending": pending,
            "device.pairing.approve": approve,
            "device.pairing.deny": deny}


def install_registry_routes(
    api: FastAPI,
    *,
    registry: UniversalCapabilityRegistry,
    pc_controller: PCController,
    core_service,
    buddy,
    perception=None,
    device_monitor=None,
    device_store: DevicePairingStore | None = None,
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

    # Screen perception (capture always available; OCR depends on installed engine).
    if perception is not None:
        registry.register(
            Capability(
                id="screen.read",
                name="Read screen",
                description="Capture the primary monitor as an image.",
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                category="screen-perception",
                handler=_capture_handler(perception),
            )
        )
        registry.register(
            Capability(
                id="screen.ocr",
                name="OCR screenshot",
                description="Extract text from a captured image.",
                input_schema={
                    "type": "object",
                    "properties": {"image_path": {"type": "string"}},
                    "required": ["image_path"],
                },
                output_schema={"type": "object"},
                category="screen-perception",
                handler=_ocr_handler(perception),
            )
        )

    # Device connection handlers (no hardware is touched unless monitor.start is
    # explicitly called — the serial port is only opened then).
    if device_monitor is not None:
        registry.register(
            Capability(
                id="device.status",
                name="Device status",
                description="Piblo handheld connection state (port, reconnects, last seen).",
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                category="connected-devices",
                handler=_device_status_handler(device_monitor),
            )
        )
        registry.register(
            Capability(
                id="device.monitor.start",
                name="Start device monitor",
                description="Open the handheld serial port and follow reconnects.",
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                category="connected-devices",
                required_permissions=["device_control"],
                handler=_device_monitor_handler(device_monitor, "start"),
            )
        )
        registry.register(
            Capability(
                id="device.monitor.stop",
                name="Stop device monitor",
                description="Close the handheld serial port.",
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                category="connected-devices",
                required_permissions=["device_control"],
                handler=_device_monitor_handler(device_monitor, "stop"),
            )
        )

    # Device pairing over the shared contract (approve/deny device requests
    # from /pair; the store is shared with the pairing app so state is one).
    if device_store is not None:
        handlers = _device_pairing_handlers(device_store)
        for cap_id, handler in handlers.items():
            registry.register(
                Capability(
                    id=cap_id,
                    name=cap_id.replace("_", " ").title(),
                    description="Device pairing request handling.",
                    input_schema={
                        "type": "object",
                        "properties": {"request_id": {"type": "string"}, "code": {"type": "string"}},
                        "required": ["request_id"],
                    },
                    output_schema={"type": "object"},
                    category="connected-devices",
                    required_permissions=["device_control"],
                    handler=handler,
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
