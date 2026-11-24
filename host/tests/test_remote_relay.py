import hashlib
import re
import time

import pytest
from fastapi.testclient import TestClient

from mazhost import private_mode, remote_connector
from mazhost.remote_connector import filtered_tasks, origin
from mazhost import remote_relay
from mazhost.remote_relay import SESSION_COOKIE, add_account, create_relay, database

ORIGIN = "https://remote.example"
FORM = {"Origin": ORIGIN}
ALICE, BOB = "alice@example.com", "bob@example.com"


def relay(path, **kwargs):
    """Relay whose 'emails' land in the returned dict instead of Resend."""
    inbox = {}
    return create_relay(path, ORIGIN, mailer=inbox.__setitem__, **kwargs), inbox


def sign_in(browser, inbox, email):
    assert browser.post("/login", headers=FORM, data={"email": email}).status_code == 200
    response = browser.post("/login/code", headers=FORM, data={"email": email, "code": inbox.pop(email)}, follow_redirects=False)
    assert response.status_code == 303
    return response


def test_pair_publish_isolate_and_revoke(tmp_path):
    path = tmp_path / "relay.sqlite3"
    app, inbox = relay(path)
    alice, bob = (TestClient(app, base_url=ORIGIN) for _ in range(2))
    for email in (ALICE, BOB):
        add_account(path, email)
    assert alice.post("/pair", headers=FORM).status_code == 401
    assert alice.post("/login", headers={"Origin": "https://wrong.example"}, data={"email": ALICE}).status_code == 403
    assert not inbox
    for browser, email in ((alice, ALICE), (bob, BOB)):
        response = sign_in(browser, inbox, email)
        assert {"secure", "httponly", "samesite=strict"} <= set(response.headers["set-cookie"].lower().split("; "))
    pair_request = alice.build_request("POST", "/pair", headers=FORM)
    del pair_request.headers["content-length"]
    page = alice.send(pair_request)
    code = re.search(r'id="pairing-code">([^<]+)', page.text)[1]
    assert alice.post("/api/pair/preview", json={"code": code}).json() == {"account": ALICE}
    claim = {"code": code, "account": ALICE, "host": "Alice PC"}
    assert bob.post("/api/pair/claim", json={**claim, "account": BOB}).status_code == 404
    response = alice.post("/api/pair/claim", json=claim)
    assert response.status_code == 201
    key = response.json()["device_key"]
    device = {"Authorization": f"Bearer {key}"}
    assert alice.post("/api/pair/claim", json=claim).status_code == 404

    board = {"tasks": [
        {"id": "mc-fixture-1", "title": "private prompt", "detail": "private log", "agent": "Claude 2", "status": "running", "progress": 42},
        {"id": "other-agent-task", "title": " unrelated ", "status": "done"},
    ]}
    snapshot = {"tasks": filtered_tasks(board)}
    task_id = hashlib.sha256(b"mc-fixture-1").hexdigest()[:16]
    assert snapshot["tasks"] == [{"id": task_id, "worker": "Claude", "status": "running", "progress": 42}]
    assert alice.post("/api/snapshot", json=snapshot).status_code == 401
    assert alice.post("/api/snapshot", headers=device, json={"tasks": [{**snapshot["tasks"][0], "detail": "private log"}]}).status_code == 422
    assert alice.post("/api/snapshot", headers=device, json=snapshot).status_code == 200
    page = alice.get("/").text
    assert "Alice PC" in page and "Claude · Running · 42%" in page
    assert "private prompt" not in page and "private log" not in page
    host = re.search(r"/hosts/([0-9a-f]+)/revoke", page)[1]
    tasks_url = f"/api/hosts/{host}/tasks"
    assert alice.get(tasks_url).json()["online"] is True
    assert "Alice PC" not in bob.get("/").text
    assert bob.get(tasks_url).status_code == 404
    assert bob.post(f"/hosts/{host}/revoke", headers=FORM).status_code == 404

    with database(path) as db:
        db.execute("UPDATE hosts SET seen=?", (int(time.time()) - 31,))
    assert alice.get(tasks_url).json()["online"] is False
    assert "Offline" in alice.get("/").text
    assert alice.post("/api/snapshot", headers=device, json=snapshot).status_code == 200
    restarted = TestClient(create_relay(path, ORIGIN), base_url=ORIGIN, cookies={SESSION_COOKIE: alice.cookies[SESSION_COOKIE]})
    assert restarted.get(tasks_url).json()["online"] is True
    assert restarted.post(f"/hosts/{host}/revoke", headers=FORM, follow_redirects=False).status_code == 303
    assert alice.post("/api/snapshot", headers=device, json=snapshot).status_code == 401
    assert restarted.post("/logout", headers=FORM, follow_redirects=False).status_code == 303
    assert alice.get(tasks_url).status_code == 401


def test_pair_expiry_and_transport_guards(tmp_path):
    path = tmp_path / "relay.sqlite3"
    with pytest.raises(ValueError, match="HTTPS"):
        create_relay(path, "http://remote.example", insecure_local=True)
    with pytest.raises(ValueError, match="loopback"):
        origin("https://external.example", local=True)
    app, inbox = relay(path)
    client = TestClient(app, base_url=ORIGIN)
    add_account(path, ALICE)
    sign_in(client, inbox, ALICE)
    code = re.search(r'id="pairing-code">([^<]+)', client.post("/pair", headers=FORM).text)[1]
    with database(path) as db:
        db.execute("UPDATE pairing SET expires=0")
    assert client.post("/api/pair/preview", json={"code": code}).status_code == 404
    assert client.post("/api/pair/claim", json={"code": code, "account": ALICE, "host": "PC"}).status_code == 404
    assert client.post("/api/snapshot", content=b"x" * 16001).status_code == 413


def test_connector_rechecks_core_before_publishing_outside_lan(monkeypatch):
    calls = []
    current_privacy = {"private_mode": True}

    def request(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/buddy/tasks"):
            return {"tasks": []}
        if url.endswith("/privacy"):
            return current_privacy
        return {"ok": True}

    monkeypatch.setattr(remote_connector, "request_json", request)
    relay = "https://relay.example"
    core = "http://127.0.0.1:8787"
    for mode in (True, None, False, True):
        current_privacy["private_mode"] = mode
        if mode is False:
            assert remote_connector.publish(relay, core, "core-secret", "device-key") == 0
        else:
            with pytest.raises(private_mode.PrivateModeError):
                remote_connector.publish(relay, core, "core-secret", "device-key")
    outside = [(url, kwargs) for url, kwargs in calls if url.startswith(relay)]
    assert outside == [(relay + "/api/snapshot", {"body": {"tasks": []}, "token": "device-key"})]


def test_email_code_sign_in_rules(tmp_path):
    path = tmp_path / "relay.sqlite3"
    app, inbox = relay(path)
    add_account(path, " Alice@Example.com ")
    browser = TestClient(app, base_url=ORIGIN)

    # Unknown and allowed emails get the same page; only the allowed one gets mail.
    unknown = browser.post("/login", headers=FORM, data={"email": "eve@example.com"})
    allowed = browser.post("/login", headers=FORM, data={"email": ALICE.upper()})
    assert unknown.status_code == allowed.status_code == 200
    assert unknown.text.replace("eve@example.com", ALICE) == allowed.text
    assert list(inbox) == [ALICE] and re.fullmatch(r"\d{6}", inbox[ALICE])
    assert browser.post("/login", headers=FORM, data={"email": "not-an-email"}).status_code == 400

    # Wrong codes count; the fifth wrong try kills even the right code.
    code = inbox.pop(ALICE)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(remote_relay.CODE_TRIES):
        bad = browser.post("/login/code", headers=FORM, data={"email": ALICE, "code": wrong})
        assert bad.status_code == 401 and SESSION_COOKIE not in bad.cookies
    assert browser.post("/login/code", headers=FORM, data={"email": ALICE, "code": code}).status_code == 401

    # A fresh code works once; replay fails.
    sign_in(browser, inbox, ALICE)
    browser.post("/logout", headers=FORM)
    browser.post("/login", headers=FORM, data={"email": ALICE})
    code = inbox.pop(ALICE)
    assert browser.post("/login/code", headers=FORM, data={"email": ALICE, "code": code}, follow_redirects=False).status_code == 303
    assert browser.post("/login/code", headers=FORM, data={"email": ALICE, "code": code}).status_code == 401

    # Expired codes fail; the code is stored hashed, never in plain text.
    with database(path) as db:
        db.execute("DELETE FROM limits")  # three codes already used this window
    browser.post("/login", headers=FORM, data={"email": ALICE})
    code = inbox.pop(ALICE)
    with database(path) as db:
        assert code not in str([tuple(row) for row in db.execute("SELECT * FROM login_codes")])
        db.execute("UPDATE login_codes SET expires=0")
    assert browser.post("/login/code", headers=FORM, data={"email": ALICE, "code": code}).status_code == 401
    assert browser.post("/login/code", headers={"Origin": "https://wrong.example"}, data={"email": ALICE, "code": code}).status_code == 403

    # Code requests are limited per email.
    statuses = [browser.post("/login", headers=FORM, data={"email": ALICE}).status_code for _ in range(4)]
    assert statuses[-1] == 429


def test_request_limits_follow_proxy_address(tmp_path):
    path = tmp_path / "relay.sqlite3"
    app, inbox = relay(path, behind_proxy=True)
    browser = TestClient(app, base_url=ORIGIN)
    first = {**FORM, "x-forwarded-for": "198.51.100.7"}
    statuses = [browser.post("/login", headers=first, data={"email": f"user{n}@example.com"}).status_code
                for n in range(remote_relay.LIMITS["code-ip"] + 1)]
    assert statuses[-1] == 429 and set(statuses[:-1]) == {200}
    other = {**FORM, "x-forwarded-for": "spoofed, 203.0.113.9"}
    assert browser.post("/login", headers=other, data={"email": "user0@example.com"}).status_code == 200
    claims = [browser.post("/api/pair/claim", headers={"x-forwarded-for": "198.51.100.7"},
                           json={"code": "a" * 24, "account": ALICE, "host": "PC"}).status_code
              for _ in range(remote_relay.LIMITS["pair-ip"] + 1)]
    assert claims[-1] == 429 and set(claims[:-1]) == {404}


def test_mail_failure_and_private_mode(tmp_path, monkeypatch):
    path = tmp_path / "relay.sqlite3"

    def broken(email, code):
        raise RuntimeError(f"provider said no to {code}")

    app = create_relay(path, ORIGIN, mailer=broken)
    add_account(path, ALICE)
    browser = TestClient(app, base_url=ORIGIN)
    response = browser.post("/login", headers=FORM, data={"email": ALICE})
    assert response.status_code == 503 and "provider" not in response.text
    with database(path) as db:
        assert db.execute("SELECT COUNT(*) FROM login_codes").fetchone()[0] == 0

    monkeypatch.setenv("RESEND_API_KEY", "re_test_not_real")
    monkeypatch.setattr(private_mode, "enabled", lambda: True)
    with pytest.raises(private_mode.PrivateModeError):
        remote_relay.resend_mailer(ALICE, "123456")


def test_old_access_key_database_is_refused(tmp_path):
    import sqlite3
    path = tmp_path / "relay.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE accounts (name TEXT PRIMARY KEY, access_hash TEXT UNIQUE NOT NULL)")
    with pytest.raises(ValueError, match="new --db"):
        create_relay(path, ORIGIN)
