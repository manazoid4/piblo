"""HTTP routes and registry capabilities for Find My Devices.

Routes (all behind Core's bearer token, like every other Core route):
  GET    /finddevices                    book + unverified nearby adverts
  POST   /finddevices/devices            add / update a device
  DELETE /finddevices/devices/{id}
  POST   /finddevices/sightings          observers (Android BLE scan) report signal
  GET    /finddevices/devices/{id}       one device with live proximity (hot/cold poll)
  POST   /finddevices/devices/{id}/ring  ring a Piblo phone / handheld
  POST   /finddevices/scan               optional PC BLE scan (needs `bleak`)

Capabilities: devices.list, devices.find, devices.ring, devices.handoff.
"""

from __future__ import annotations

import logging
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .buddy import AlertIn
from .find_devices import (APPLE_FINDMY_URL, GOOGLE_FIND_HUB_URL, DeviceBook, answer_unknown,
                           range_text, spoken_answer)
from .universal_registry import Capability, Event, EventBus, UniversalCapabilityRegistry

log = logging.getLogger("piblo.finddevices")
HANDOFF_URLS = {"apple_findmy": APPLE_FINDMY_URL, "google_find_hub": GOOGLE_FIND_HUB_URL}


class DeviceBody(BaseModel):
    id: str = Field(default="", max_length=40)
    name: str = Field(min_length=1, max_length=60)
    kind: str = "other"
    aliases: list[str] = Field(default_factory=list, max_length=6)
    providers: dict[str, dict] = Field(default_factory=dict)


class SightingsBody(BaseModel):
    observer: str = Field(min_length=1, max_length=60)
    sightings: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class ScanBody(BaseModel):
    seconds: float = Field(default=4.0, ge=1.0, le=15.0)


async def pc_ble_scan(seconds: float) -> dict:
    """Scan from the PC's Bluetooth adapter if `bleak` is installed. Never fakes results."""
    try:
        from bleak import BleakScanner  # type: ignore
    except Exception:
        return {"available": False, "reason": "bleak_not_installed", "sightings": []}
    try:
        found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    except Exception as error:  # adapter off, no permission, no radio
        return {"available": False, "reason": f"scan_failed: {type(error).__name__}", "sightings": []}
    now = time.time()
    rows = []
    for device, adv in found.values():
        row = {"at": now, "address": device.address, "name": adv.local_name or device.name or "",
               "rssi": adv.rssi, "tx_power": adv.tx_power}
        apple = (adv.manufacturer_data or {}).get(0x004C)
        if apple:
            row["apple_data"] = bytes(apple).hex()
        rows.append(row)
    return {"available": True, "sightings": rows}


class FindDevicesService:
    def __init__(self, book: DeviceBook, buddy, bus: EventBus | None = None,
                 open_url: Callable[[str], Any] = webbrowser.open) -> None:
        self.book = book
        self.buddy = buddy
        self.bus = bus
        self.open_url = open_url

    def find(self, query: str) -> dict:
        query = (query or "").strip()
        if not query:
            raise ValueError("find_query_required")
        matches = self.book.resolve(query)
        if not matches:
            return answer_unknown(query, self.book.nearby_unassigned())
        dev = matches[0]
        model_id = (dev.get("providers", {}).get("bluetooth") or {}).get("apple_model_id", "")
        lookalikes = self.book.apple_lookalikes(model_id) if dev["kind"] in ("earbuds", "headphones") else []
        return {"found": True, "device": dev, "others": matches[1:], "lookalikes": lookalikes[:3],
                "range": range_text(dev["proximity"]), "reply": spoken_answer(dev, lookalikes)}

    def ring(self, dev_id: str) -> dict:
        try:
            dev = self.book.describe(dev_id)
        except KeyError:
            raise ValueError("device_not_found")
        providers = dev.get("providers", {})
        if "piblo_phone" in providers:
            posted = self.buddy.post_alert(AlertIn(
                source="find_phone", kind="info", title=f"Find {dev['name']}"[:60],
                priority="urgent", actions=["ack"], expires_in=300))
            return {"rang": True, "provider": "piblo_phone", "alert_id": posted.get("id"),
                    "reply": f"Ringing {dev['name']} now. Tap Stop on the phone when you find it."}
        if "piblo_handheld" in providers:
            posted = self.buddy.post_alert(AlertIn(
                source="find_device", kind="info", title="Piblo: here I am!", priority="urgent",
                actions=["ack"], expires_in=300))
            return {"rang": True, "provider": "piblo_handheld", "alert_id": posted.get("id"),
                    "reply": f"{dev['name']} is beeping. Press any key on it to stop."}
        for provider in ("apple_findmy", "google_find_hub"):
            if provider in providers:
                return {"rang": False, "provider": provider, "handoff_url": HANDOFF_URLS[provider],
                        "reply": ("Piblo can't ring this directly. Open "
                                  + ("Apple Find My" if provider == "apple_findmy" else "Google Find Hub")
                                  + " and press Play sound.")}
        raise ValueError("device_cannot_ring")

    def handoff(self, provider: str, target: str = "pc") -> dict:
        url = HANDOFF_URLS.get(provider)
        if not url:
            raise ValueError("handoff_provider_invalid")
        if target == "pc":
            opened = bool(self.open_url(url))
            return {"opened": opened, "target": "pc", "url": url,
                    "reply": "Opened it in your browser. Sign in there if asked; Piblo never sees your password."}
        return {"opened": False, "target": target, "url": url, "reply": "Tap the link to open it."}


def install_find_devices(api, *, registry: UniversalCapabilityRegistry, buddy, control_dir: str | Path,
                         open_url: Callable[[str], Any] = webbrowser.open) -> FindDevicesService:
    book = DeviceBook(Path(control_dir).expanduser() / "find-devices.json")
    service = FindDevicesService(book, buddy, registry.event_bus, open_url)
    router = APIRouter(prefix="/finddevices", tags=["find devices"])

    @router.get("")
    def overview():
        return {"ok": True, "devices": book.list(), "nearby": book.nearby_unassigned()}

    @router.post("/devices")
    def add(body: DeviceBody):
        try:
            return {"ok": True, "device": book.add(body.model_dump())}
        except ValueError as error:
            raise HTTPException(422, str(error))
        except OverflowError as error:
            raise HTTPException(409, str(error))

    @router.delete("/devices/{dev_id}")
    def remove(dev_id: str):
        if not book.remove(dev_id):
            raise HTTPException(404, "device_not_found")
        return {"ok": True}

    @router.get("/devices/{dev_id}")
    def one(dev_id: str):
        try:
            return {"ok": True, "device": book.describe(dev_id)}
        except KeyError:
            raise HTTPException(404, "device_not_found")

    @router.post("/devices/{dev_id}/ring")
    def ring(dev_id: str):
        try:
            return {"ok": True, **service.ring(dev_id)}
        except ValueError as error:
            raise HTTPException(404 if str(error) == "device_not_found" else 422, str(error))

    @router.post("/sightings")
    async def sightings(body: SightingsBody):
        result = book.report(body.observer, body.sightings)
        if registry.event_bus and result["matched"]:
            await registry.event_bus.publish(Event(type="devices.sighted", source="finddevices",
                                                   payload={"observer": body.observer, **result}))
        return result

    @router.post("/scan")
    async def scan(body: ScanBody):
        result = await pc_ble_scan(body.seconds)
        if result["sightings"]:
            book.report("pc", result["sightings"])
        return {"ok": True, "available": result["available"], "reason": result.get("reason", ""),
                "seen": len(result["sightings"]), "nearby": book.nearby_unassigned()}

    api.include_router(router)

    def _list(args, context):
        return {"devices": book.list(), "nearby": book.nearby_unassigned()}

    def _find(args, context):
        return service.find(str(args.get("query", "")))

    def _ring(args, context):
        dev_id = str(args.get("device_id", ""))
        if not dev_id and args.get("query"):
            matches = book.resolve(str(args["query"]))
            if not matches:
                raise ValueError("device_not_found")
            dev_id = matches[0]["id"]
        return service.ring(dev_id)

    def _handoff(args, context):
        return service.handoff(str(args.get("provider", "")), str(args.get("target", "pc")))

    obj = {"type": "object"}
    registry.register(Capability(
        id="devices.list", name="List my devices", category="find-devices", risk="read",
        description="List the user's devices with last seen time and Bluetooth proximity.",
        input_schema={"type": "object", "properties": {}}, output_schema=obj,
        required_permissions=["find_devices"], handler=_list))
    registry.register(Capability(
        id="devices.find", name="Find a device", category="find-devices", risk="read",
        description="Find one of the user's devices (AirPods, phone, keys tag...): proximity, last seen, actions.",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        output_schema=obj, required_permissions=["find_devices"], handler=_find))
    registry.register(Capability(
        id="devices.ring", name="Ring a device", category="find-devices", risk="act", autonomous_ok=True,
        description="Make a Piblo phone or handheld ring loudly so it can be found.",
        input_schema={"type": "object", "properties": {"device_id": {"type": "string"},
                                                       "query": {"type": "string"}}},
        output_schema=obj, required_permissions=["find_devices", "notifications"], handler=_ring))
    registry.register(Capability(
        id="devices.handoff", name="Open Find My / Find Hub", category="find-devices", risk="act",
        autonomous_ok=True,
        description="Open Apple Find My or Google Find Hub in the user's own browser.",
        input_schema={"type": "object", "properties": {"provider": {"type": "string"},
                                                       "target": {"type": "string"}},
                      "required": ["provider"]},
        output_schema=obj, required_permissions=["find_devices"], handler=_handoff))
    api.state.find_devices = service
    return service


__all__ = ["install_find_devices", "FindDevicesService", "pc_ble_scan"]
