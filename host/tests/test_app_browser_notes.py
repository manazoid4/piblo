import asyncio
import types
from unittest.mock import patch

from mazhost.universal_registry import UniversalCapabilityRegistry
from mazhost.registry_routes import install_registry_routes


class FakePC:
    def __init__(self):
        self.available = True

    def perform(self, action):
        from mazhost.pc import ActionResult
        return ActionResult(action=action, label=f"mock:{action}")


class FakeCore:
    def status(self):
        return {"ok": True}


class FakeBuddy:
    def __init__(self):
        self.alerts = []

    def post_alert(self, a):
        self.alerts.append(a)


class FakeMemoryNotes:
    def __init__(self):
        self.notes = []

    def remember(self, text):
        self.notes.append(text)
        return types.SimpleNamespace(__str__=lambda s: "notes/2026-11-22.md")


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


def _install(api, registry, memory_notes=None, pc=None, core=None, buddy=None,
             device_monitor=None, device_store=None):
    install_registry_routes(
        api, registry=registry, pc_controller=pc or FakePC(),
        core_service=core or FakeCore(), buddy=buddy or FakeBuddy(),
        perception=None, device_monitor=device_monitor,
        device_store=device_store, memory_notes=memory_notes,
    )


def test_app_launch_known_app():
    started = []
    with patch("mazhost.registry_routes.shutil.which", return_value="C:/Windows/notepad.exe"), \
            patch("mazhost.registry_routes.os.startfile", side_effect=lambda p: started.append(p)):
        registry = UniversalCapabilityRegistry()
        _install(FakeApi(), registry)
        res = asyncio.run(registry.execute("app.launch", {"name": "notepad"}, {}))
    assert res["success"] is True
    assert res["result"]["app"] == "notepad"
    assert started == ["C:/Windows/notepad.exe"]


def test_app_launch_missing():
    with patch("mazhost.registry_routes.shutil.which", return_value=None):
        registry = UniversalCapabilityRegistry()
        _install(FakeApi(), registry)
        res = asyncio.run(registry.execute("app.launch", {"name": "nope"}, {}))
    assert res["success"] is False
    assert "app_not_found" in res["error"]


def test_app_launch_empty_name():
    registry = UniversalCapabilityRegistry()
    _install(FakeApi(), registry)
    res = asyncio.run(registry.execute("app.launch", {"name": ""}, {}))
    assert res["success"] is False


def test_browser_open():
    opened = []
    with patch("mazhost.registry_routes.webbrowser.open", side_effect=lambda u: opened.append(u)):
        registry = UniversalCapabilityRegistry()
        _install(FakeApi(), registry)
        res = asyncio.run(registry.execute("browser.open", {"url": "https://example.com"}, {}))
    assert res["success"] is True
    assert opened == ["https://example.com"]


def test_browser_open_rejects_non_http():
    registry = UniversalCapabilityRegistry()
    _install(FakeApi(), registry)
    res = asyncio.run(registry.execute("browser.open", {"url": "file:///C:/secret"}, {}))
    assert res["success"] is False


def test_browser_search():
    opened = []
    with patch("mazhost.registry_routes.webbrowser.open", side_effect=lambda u: opened.append(u)):
        registry = UniversalCapabilityRegistry()
        _install(FakeApi(), registry)
        res = asyncio.run(registry.execute("browser.search", {"query": "ESP32 boards"}, {}))
    assert res["success"] is True
    assert opened[0].startswith("https://duckduckgo.com/?q=")
    assert "ESP32" in opened[0]


def test_notes_create():
    notes = FakeMemoryNotes()
    registry = UniversalCapabilityRegistry()
    _install(FakeApi(), registry, memory_notes=notes)
    res = asyncio.run(registry.execute("notes.create", {"text": "Buy milk"}, {}))
    assert res["success"] is True
    assert res["result"]["note"] == "Buy milk"
    assert notes.notes == ["Buy milk"]


def test_notes_create_empty():
    notes = FakeMemoryNotes()
    registry = UniversalCapabilityRegistry()
    _install(FakeApi(), registry, memory_notes=notes)
    res = asyncio.run(registry.execute("notes.create", {"text": ""}, {}))
    assert res["success"] is False


def test_capabilities_list_includes_new():
    notes = FakeMemoryNotes()
    registry = UniversalCapabilityRegistry()
    _install(FakeApi(), registry, memory_notes=notes)
    ids = {c["id"] for c in registry.list_all()}
    assert {"app.launch", "browser.open", "browser.search", "notes.create"} <= ids
