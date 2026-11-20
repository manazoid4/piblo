"""Device tab: live screen proxy, remote key/update actions, version comparison."""
import httpx
import pytest

from mazhost.config import Settings
from mazhost.core import CoreError, MazCore


def _service(handler, **kw):
    settings = Settings(token="t" * 32, cardputer_url="http://piblo.local", **kw)
    return MazCore(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_known_device_ip_is_tried_before_mdns():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(200, content=b"\0" * (240 * 135 * 2))

    svc = _service(handler)
    svc.note_device("192.168.1.42")
    assert len(svc.cardputer_screen()) == 64800
    assert seen == ["192.168.1.42"]


def test_falls_back_to_configured_url_and_ignores_public_ips():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "10.0.0.9":
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"version": "1.0.2"})

    svc = _service(handler)
    svc.note_device("8.8.8.8")
    assert svc.device_ip is None
    svc.note_device("10.0.0.9")
    assert svc.cardputer_status()["version"] == "1.0.2"
    assert seen == ["10.0.0.9", "piblo.local"]


def test_action_forwards_form_and_token():
    got = {}

    def handler(request):
        got["body"] = request.content.decode()
        got["token"] = request.headers.get("X-MAZ-Token")
        return httpx.Response(423, text="Approvals are decided on the device itself.")

    out = _service(handler).cardputer_action("key:40,0,0")
    assert got["body"] == "action=key%3A40%2C0%2C0"
    assert got["token"] == "t" * 32
    assert out == {"ok": False, "status": 423, "text": "Approvals are decided on the device itself."}


def test_unreachable_device_raises_core_error():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(CoreError):
        _service(handler).cardputer_screen()


def test_action_whitelist():
    from mazhost.app import _DEVICE_ACTION
    ok = ["update", "fwcheck", "reboot", "key:4,97,0", "key:82,0,1", "open:inbox"]
    bad = ["launcher", "pc:lock", "key:4,97", "update;reboot", "open:../x", "key:4,97,0,1", "reconnect"]
    assert all(_DEVICE_ACTION.fullmatch(a) for a in ok)
    assert not any(_DEVICE_ACTION.fullmatch(a) for a in bad)
