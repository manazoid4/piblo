from mazhost.registry_routes import install_registry_routes
from mazhost.universal_registry import UniversalCapabilityRegistry


class FakeMonitor:
    def __init__(self):
        self.started = False
        self.stopped = False

    def status(self):
        return {"connection_state": "connected", "port": "COM5", "reconnect_attempts": 1}

    def start(self):
        self.started = True
        return self.status()

    def stop(self):
        self.stopped = True
        return self.status()


class FakeStore:
    def __init__(self):
        self.pends = [{"request_id": "r1", "code": "123456", "name": "Piblo-123456", "device_id": "piblo-123456", "ip": "1.2.3.4", "age_s": 5}]
        self.decided = []

    def pending(self):
        return list(self.pends)

    def decide(self, request_id, approve, code=None):
        self.decided.append((request_id, approve, code))
        return request_id in (p["request_id"] for p in self.pends)


def test_device_capabilities_register_and_execute():
    registry = UniversalCapabilityRegistry()
    monitor = FakeMonitor()
    store = FakeStore()
    import types
    api = types.SimpleNamespace()
    routes = []

    class FakeApi:
        def __init__(self):
            self.routes = []
        def get(self, path):
            def deco(fn):
                self.routes.append(("GET", path, fn))
                return fn
            return deco
        def post(self, path):
            def deco(fn):
                self.routes.append(("POST", path, fn))
                return fn
            return deco

    fake_api = FakeApi()
    install_registry_routes(fake_api, registry=registry, pc_controller=None,
                            core_service=None, buddy=None,
                            device_monitor=monitor, device_store=store)

    ids = {c["id"] for c in registry.list_all()}
    assert {"device.status", "device.monitor.start", "device.monitor.stop",
            "device.pairing.pending", "device.pairing.approve", "device.pairing.deny"} <= ids

    import asyncio
    res = asyncio.run(registry.execute("device.status", {}, {}))
    assert res["success"] is True and res["result"]["connection_state"] == "connected"

    res = asyncio.run(registry.execute("device.monitor.start", {}, {}))
    assert res["success"] and monitor.started

    res = asyncio.run(registry.execute("device.monitor.stop", {}, {}))
    assert res["success"] and monitor.stopped

    res = asyncio.run(registry.execute("device.pairing.pending", {}, {}))
    assert res["success"] and len(res["result"]["requests"]) == 1

    res = asyncio.run(registry.execute("device.pairing.approve",
                                       {"request_id": "r1", "code": "123456"}, {}))
    assert res["success"] and res["result"]["state"] == "approved"
    assert store.decided[-1] == ("r1", True, "123456")

    res = asyncio.run(registry.execute("device.pairing.deny",
                                       {"request_id": "r1"}, {}))
    assert res["success"] and res["result"]["state"] == "denied"

    res = asyncio.run(registry.execute("device.pairing.approve",
                                       {"request_id": "nope"}, {}))
    assert res["success"] is False

    res = asyncio.run(registry.execute("device.pairing.approve", {}, {}))
    assert res["success"] is False

    print("ALL DEVICE CHECKS PASSED")
