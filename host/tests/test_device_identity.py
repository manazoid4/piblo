"""Device identity: the Cardputer declares its board kind in /api/status."""

from pathlib import Path

import httpx

from mazhost.config import Settings
from mazhost.core import MazCore

ROOT = Path(__file__).resolve().parents[2]


def test_linked_portal_declares_device_kind():
    # portal_v3.cpp is the only portal linked into firmware (platformio.ini excludes
    # portal.cpp/portal_v2.cpp), so this is the statusJson remote MCP consumers see.
    source = (ROOT / "src/net/portal_v3.cpp").read_text(encoding="utf-8-sig")
    assert '\\"kind\\":\\"cardputer\\"' in source


def test_core_passes_device_kind_through_unchanged():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "version": "1.5.0", "kind": "cardputer"})

    service = MazCore(
        Settings(token="t" * 32, cardputer_url="http://piblo.local"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert service.cardputer_status()["kind"] == "cardputer"
