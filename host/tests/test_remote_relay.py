import hashlib
import re
import time

import pytest
from fastapi.testclient import TestClient

from mazhost.remote_connector import filtered_tasks, origin
from mazhost.remote_relay import SESSION_COOKIE, add_account, create_relay, database

ORIGIN = "https://remote.example"
FORM = {"Origin": ORIGIN}


def test_pair_publish_isolate_and_revoke(tmp_path):
    path = tmp_path / "relay.sqlite3"
    app = create_relay(path, ORIGIN)
    alice, bob = (TestClient(app, base_url=ORIGIN) for _ in range(2))
    credentials = [add_account(path, name) for name in ("alice", "bob")]
    assert alice.post("/pair", headers=FORM).status_code == 401
    assert alice.post("/login", headers={"Origin": "https://wrong.example"}, data={"key": credentials[0]}).status_code == 403
    oversized_key = alice.post("/login", headers=FORM, data={"key": credentials[0] + " "})
    assert oversized_key.status_code == 422 and credentials[0] not in oversized_key.text
    for browser, key in zip((alice, bob), credentials):
        response = browser.post("/login", headers=FORM, data={"key": key}, follow_redirects=False)
        assert response.status_code == 303
        assert {"secure", "httponly", "samesite=strict"} <= set(response.headers["set-cookie"].lower().split("; "))
    page = alice.post("/pair", headers=FORM)
    code = re.search(r'id="pairing-code">([^<]+)', page.text)[1]
    assert alice.post("/api/pair/preview", json={"code": code}).json() == {"account": "alice"}
    claim = {"code": code, "account": "alice", "host": "Alice PC"}
    assert bob.post("/api/pair/claim", json={**claim, "account": "bob"}).status_code == 404
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
    app = create_relay(path, ORIGIN)
    client = TestClient(app, base_url=ORIGIN)
    key = add_account(path, "alice")
    client.post("/login", headers=FORM, data={"key": key})
    code = re.search(r'id="pairing-code">([^<]+)', client.post("/pair", headers=FORM).text)[1]
    with database(path) as db:
        db.execute("UPDATE pairing SET expires=0")
    assert client.post("/api/pair/preview", json={"code": code}).status_code == 404
    assert client.post("/api/pair/claim", json={"code": code, "account": "alice", "host": "PC"}).status_code == 404
    assert client.post("/api/snapshot", content=b"x" * 16001).status_code == 413
