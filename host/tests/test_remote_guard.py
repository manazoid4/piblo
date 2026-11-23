from __future__ import annotations

from fastapi.testclient import TestClient

from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.security import AuthFailureLimiter, is_lan_ip

TOKEN = "test-token-that-is-not-default"
FUNNEL = {"X-Forwarded-For": "203.0.113.9", "Tailscale-Funnel-Request": "?1", "X-Forwarded-Proto": "https"}


def _client(tmp_path, peer="127.0.0.1"):
    app = create_app(Settings(token=TOKEN, work_dir=str(tmp_path / "work")))

    async def with_peer(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope["client"] = (peer, 50000)
        await app(scope, receive, send)

    return TestClient(with_peer)


def test_lan_classification():
    assert all(is_lan_ip(x) for x in ["127.0.0.1", "::1", "192.168.1.5", "10.0.0.2", "100.101.1.1", "169.254.1.1"])
    assert not any(is_lan_ip(x) for x in ["8.8.8.8", "93.184.216.34", "garbage"])


def test_health_minimal_without_token_full_with_token(tmp_path):
    c = _client(tmp_path)
    r = c.get("/health", headers=FUNNEL)
    assert r.status_code == 200 and set(r.json()) == {"ok", "name", "version"}
    full = c.get("/health", headers={**FUNNEL, "Authorization": f"Bearer {TOKEN}"}).json()
    assert "authority" in full and "llm" in full


def test_remote_routes_need_token(tmp_path):
    c = _client(tmp_path)
    for path in ["/models", "/core/projects", "/device", "/authority/grants", "/work/templates"]:
        assert c.get(path, headers=FUNNEL).status_code == 401, path
    assert c.get("/models", headers={**FUNNEL, "Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_remote_docs_and_pairing_blocked(tmp_path):
    c = _client(tmp_path)
    for path in ["/docs", "/openapi.json", "/redoc", "/pair/", "/pair/docs"]:
        assert c.get(path, headers=FUNNEL).status_code in (403, 404), path
    assert c.post("/pair/claim", json={"code": "AAAAAAAA"}, headers=FUNNEL).status_code == 403
    assert c.post("/pair/start", headers=FUNNEL).status_code == 401


def test_local_still_works(tmp_path):
    c = _client(tmp_path)
    assert c.get("/docs").status_code == 200
    assert c.get("/pair/").status_code == 200


def test_xff_ignored_from_non_loopback_peer(tmp_path):
    c = _client(tmp_path, peer="8.8.8.8")  # direct internet peer spoofing a LAN XFF
    assert c.get("/docs", headers={"X-Forwarded-For": "192.168.1.5"}).status_code == 404


def test_rate_limit_on_auth_failures_per_ip(tmp_path):
    c = _client(tmp_path)
    codes = [c.get("/models", headers=FUNNEL).status_code for _ in range(12)]
    assert codes[:9] == [401] * 9 and 429 in codes
    other = {**FUNNEL, "X-Forwarded-For": "198.51.100.7"}
    assert c.get("/models", headers=other).status_code == 401


def test_limiter_lockout_expires():
    t = [0.0]
    lim = AuthFailureLimiter(3, 60, 30, clock=lambda: t[0])
    for _ in range(3):
        lim.record_failure("1.2.3.4")
    assert lim.retry_after("1.2.3.4") > 0
    t[0] = 31
    assert lim.retry_after("1.2.3.4") == 0


def test_secure_cookie_over_funnel_https(tmp_path):
    c = _client(tmp_path)
    r = c.post("/control/session/login", data={"token": TOKEN}, headers=FUNNEL, follow_redirects=False)
    assert r.status_code == 303 and "secure" in r.headers["set-cookie"].lower()


def test_browser_code_grants_one_scoped_session_without_core_token(tmp_path):
    c = _client(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert c.post("/authority/browser-code").status_code == 401
    assert c.post("/authority/browser-code", headers={**auth, **FUNNEL}).status_code == 403
    assert c.post("/authority/browser-code", headers={**auth, "X-Forwarded-For": "100.100.1.2"}).status_code == 403
    page = c.get("/control/")
    assert "Sign in to Piblo" in page.text and 'name="token"' not in page.text
    issue = c.post("/authority/browser-code", headers=auth)
    assert issue.status_code == 200 and issue.headers["cache-control"] == "no-store"
    code = issue.json()["code"]
    assert c.post("/pair/claim", json={"code": code}).status_code == 400
    failed = c.post("/control/session/code", data={"code": "wrong"}, headers=FUNNEL)
    assert failed.status_code == 401 and "set-cookie" not in failed.headers
    conn = {"X-Forwarded-For": "100.100.1.2", "X-Forwarded-Proto": "https", "User-Agent": "Browser"}
    signed = c.post("https://testserver/control/session/code", data={"code": code.lower()}, headers=conn, follow_redirects=False)
    assert signed.status_code == 303 and TOKEN not in signed.text
    assert all(part in signed.headers["set-cookie"].lower() for part in ("httponly", "secure", "samesite=strict", "path=/control"))
    assert c.get("https://testserver/control/api/state", headers={**conn, "X-MAZ-Control": "1"}).status_code == 200
    assert c.get("https://testserver/authority/grants", headers=conn).status_code == 401
    assert c.post("https://testserver/control/session/code", data={"code": code}, headers=conn).status_code == 401
