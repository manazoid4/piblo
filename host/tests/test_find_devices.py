import asyncio
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost.find_devices import (DeviceBook, decode_apple_manufacturer, proximity, rssi_range_m,
                                  spoken_answer, answer_unknown)
from mazhost.find_devices_routes import FindDevicesService, install_find_devices
from mazhost.universal_registry import EventBus, UniversalCapabilityRegistry

# Apple proximity pairing advert (company id stripped): type 07, len 19, prefix 01,
# model 0E20 (AirPods Pro), status, pods battery 0x98 (90 %/80 %), case 0x15 (50 %).
AIRPODS_PRO = "0719010E20" + "00" + "98" + "15" + "00" * 17


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeBuddy:
    def __init__(self):
        self.alerts = []

    def post_alert(self, alert):
        self.alerts.append(alert)
        return {"ok": True, "id": f"al-{len(self.alerts)}"}


def book(clock=None):
    return DeviceBook(Path(tempfile.mkdtemp()) / "fd.json", clock=clock or Clock())


def test_decode_airpods_pro_model_and_battery():
    out = decode_apple_manufacturer(AIRPODS_PRO)
    assert out["family"] == "apple_audio"
    assert out["model"] == "AirPods Pro"
    assert out["battery_approx"] == {"pod_a": 90, "pod_b": 80, "case": 50}


def test_decode_findmy_and_garbage():
    assert decode_apple_manufacturer("1219" + "00" * 25)["family"] == "apple_findmy"
    assert decode_apple_manufacturer("zz") is None
    assert decode_apple_manufacturer("1005" + "00" * 5) is None  # nearby-info only


def test_proximity_bands_trend_and_loss():
    now = 1000.0
    far = [{"at": now - 10 + i, "rssi": -85 + i * 3, "observer": "android:p"} for i in range(8)]
    p = proximity(far, now)
    assert p["state"] == "in_range" and p["trend"] == "closer"
    assert p["band"] in ("very_close", "close", "nearby")
    lo, hi = p["range_m"]
    assert 0 < lo <= hi
    assert proximity([{"at": now - 60, "rssi": -50}], now)["state"] == "just_lost"
    assert proximity([{"at": now - 3600, "rssi": -50}], now)["state"] == "out_of_range"
    assert proximity([], now)["state"] == "never_seen"


def test_rssi_range_is_wide_and_ordered():
    lo, hi = rssi_range_m(-75)
    assert lo < hi and hi > 2 * lo


def test_resolve_and_match_by_address_then_bonded_name_only():
    clock = Clock()
    b = book(clock)
    b.add({"id": "pods", "name": "Maz's AirPods Pro", "kind": "earbuds", "aliases": ["airpods"],
           "providers": {"apple_findmy": {}, "bluetooth": {"addresses": ["aa:bb:cc:dd:ee:ff"],
                                                           "names": ["AirPods Pro"]}}})
    b.add({"id": "phone", "name": "Pixel", "kind": "phone", "providers": {"piblo_phone": {}}})
    assert b.resolve("my AirPods")[0]["id"] == "pods"
    assert b.resolve("phone")[0]["id"] == "phone"
    # A stranger's AirPods with the same name but not bonded must not match.
    r = b.report("android:p", [{"name": "AirPods Pro", "rssi": -60, "bonded": False, "at": clock.t}])
    assert r["matched"] == 0 and r["unassigned"] == 1
    r = b.report("android:p", [{"address": "AA:BB:CC:DD:EE:FF", "rssi": -50, "at": clock.t},
                               {"name": "AirPods Pro", "rssi": -52, "bonded": True, "at": clock.t}])
    assert r["matched"] == 2
    dev = b.describe("pods")
    assert dev["proximity"]["state"] == "in_range"
    assert dev["last_seen"]["observer"] == "android:p"
    # Persisted last sighting survives a restart.
    again = DeviceBook(b.path, clock=clock)
    assert again.describe("pods")["last_seen"] is not None


def test_future_timestamps_clamped_and_bad_rows_dropped():
    clock = Clock()
    b = book(clock)
    b.add({"id": "x", "name": "Tag", "kind": "tag", "providers": {"bluetooth": {"addresses": ["11:22:33:44:55:66"]}}})
    b.report("pc", [{"address": "11:22:33:44:55:66", "rssi": -60, "at": clock.t + 9999},
                    {"address": "11:22:33:44:55:66", "rssi": 999}, "nonsense"])
    dev = b.describe("x")
    assert dev["last_seen"]["ago_s"] == 0


def test_unverified_airpods_lookalike_is_never_called_yours():
    clock = Clock()
    b = book(clock)
    b.add({"id": "pods", "name": "AirPods", "kind": "earbuds",
           "providers": {"apple_findmy": {}, "bluetooth": {"apple_model_id": "0E20"}}})
    b.report("android:p", [{"address": "7A:00:00:00:00:01", "rssi": -58, "apple_data": AIRPODS_PRO,
                            "at": clock.t}])
    svc = FindDevicesService(b, FakeBuddy(), open_url=lambda url: True)
    out = svc.find("my airpods")
    assert out["found"] and out["lookalikes"]
    assert "can't prove it's yours" in out["reply"]
    assert "Apple Find My" in out["reply"]


def test_unknown_device_suggests_provider_not_guess():
    out = answer_unknown("iPhone", [])
    assert out["found"] is False
    assert out["actions"][0]["provider"] == "apple_findmy"
    assert "don't know" in out["reply"]


def test_ring_phone_uses_existing_find_phone_alert_and_handoff_opens_browser():
    b = book()
    b.add({"id": "phone", "name": "Pixel", "kind": "phone", "providers": {"piblo_phone": {}}})
    b.add({"id": "ipad", "name": "iPad", "kind": "tablet", "providers": {"apple_findmy": {}}})
    buddy, opened = FakeBuddy(), []
    svc = FindDevicesService(b, buddy, open_url=lambda url: opened.append(url) or True)
    out = svc.ring("phone")
    assert out["rang"] and buddy.alerts[0].source == "find_phone" and buddy.alerts[0].priority == "urgent"
    out = svc.ring("ipad")
    assert out["rang"] is False and out["handoff_url"].startswith("https://www.icloud.com")
    out = svc.handoff("apple_findmy")
    assert opened == ["https://www.icloud.com/find"] and "never sees your password" in out["reply"]


def test_routes_and_capabilities():
    api = FastAPI()
    reg = UniversalCapabilityRegistry(EventBus())
    buddy = FakeBuddy()
    install_find_devices(api, registry=reg, buddy=buddy, control_dir=tempfile.mkdtemp(), open_url=lambda u: True)
    c = TestClient(api)
    r = c.post("/finddevices/devices", json={"id": "phone", "name": "Pixel", "kind": "phone",
                                             "providers": {"piblo_phone": {}}})
    assert r.status_code == 200
    assert c.post("/finddevices/devices", json={"name": "x", "kind": "spaceship"}).status_code == 422
    r = c.post("/finddevices/sightings", json={"observer": "android:p", "sightings": [
        {"device_id": "phone", "rssi": -40}]})
    assert r.json()["matched"] == 1
    assert c.get("/finddevices/devices/phone").json()["device"]["proximity"]["state"] == "in_range"
    assert c.post("/finddevices/devices/phone/ring").json()["rang"] is True
    assert c.post("/finddevices/scan", json={"seconds": 1}).json()["available"] in (True, False)
    out = asyncio.run(reg.execute("devices.find", {"query": "pixel"}))
    assert out["success"] and out["result"]["found"]
    assert {"devices.list", "devices.find", "devices.ring", "devices.handoff"} <= set(reg.capabilities)
    assert c.delete("/finddevices/devices/phone").status_code == 200
    assert c.get("/finddevices/devices/phone").status_code == 404


def test_spoken_answer_for_never_seen_phone_offers_ring():
    b = book()
    b.add({"id": "phone", "name": "Pixel", "kind": "phone", "providers": {"piblo_phone": {}}})
    text = spoken_answer(b.describe("phone"))
    assert "haven't heard" in text and "ring it" in text
