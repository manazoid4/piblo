import base64
import threading
import time
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from mazhost import piblo_key as pk
from mazhost.app import create_app
from mazhost.config import Settings
from test_auth_totp import migration_uri, otp

TOKEN = "test-token-that-is-not-default"
H = {"Authorization": f"Bearer {TOKEN}"}
SECRET_B32 = base64.b32encode(b"0123456789abcdef").decode()
URI = f"otpauth://totp/GitHub:maz?secret={SECRET_B32}&issuer=GitHub"
MASTER = bytes(range(16))
UID = bytes.fromhex("04AABBCCDDEE01")


@pytest.fixture(autouse=True)
def legacy_unsigned_device(monkeypatch):
    monkeypatch.setenv("MAZ_ASK_LEGACY_DEVICE_DECIDE", "1")


@pytest.fixture
def env(tmp_path):
    app = create_app(Settings(token=TOKEN, control_dir=str(tmp_path / "control"), _env_file=None))
    svc = app.state.auth_service
    typed, clock = [], {"t": 1_000_000_010.0}
    svc.typer = typed.append
    svc.enter = lambda: typed.append("<enter>")
    svc.can_type = True
    svc.clock = lambda: clock["t"]
    svc.sleep = lambda s: clock.__setitem__("t", clock["t"] + s)
    svc.foreground = lambda: None
    c = TestClient(app)
    c.typed, c.clock, c.svc = typed, clock, svc
    return c


def add(c, uri=URI):
    r = c.post("/auth/codes/import", json={"uri": uri}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def decide(c, rid, decision="allow"):
    return c.post("/buddy/decide", json={"id": rid, "decision": decision}, headers=H)


def test_requires_token(env):
    assert env.get("/auth/codes").status_code == 401


def test_import_list_never_leaks_secret(env):
    body = add(env)
    acc = body["added"][0]
    assert set(acc) == {"id", "issuer", "label", "digits", "period", "algorithm", "match", "added_at"}
    assert add(env)["skipped"] == [{"label": "GitHub: maz", "reason": "duplicate"}]
    listing = env.get("/auth/codes", headers=H)
    assert listing.json()["locked"] is False and len(listing.json()["accounts"]) == 1
    assert SECRET_B32 not in listing.text and "secret" not in listing.text


def test_import_errors(env):
    for uri, code in (("otpauth://hotp/x?secret=" + SECRET_B32, "unsupported_type"), ("nope://x", "invalid_uri"),
                      ("otpauth://totp/x?secret=%21%21", "invalid_secret")):
        r = env.post("/auth/codes/import", json={"uri": uri}, headers=H)
        assert r.status_code == 422 and r.json()["error"] == code


def test_import_migration(env):
    payload = otp(b"A" * 10, "GitHub:maz", "GitHub", 1, 1, 2) + otp(b"B" * 10, "x", "Old", 1, 1, 1)
    body = add(env, migration_uri(payload))
    assert len(body["added"]) == 1 and body["skipped"][0]["reason"] == "unsupported_type"


def test_patch_and_delete_needs_confirm(env):
    aid = add(env)["added"][0]["id"]
    r = env.patch(f"/auth/codes/{aid}", json={"label": "work", "match": ["octocat"]}, headers=H)
    assert r.json()["account"]["label"] == "work"
    assert env.request("DELETE", f"/auth/codes/{aid}", headers=H).json()["error"] == "not_confirmed"
    assert env.request("DELETE", f"/auth/codes/{aid}", json={"confirm": True}, headers=H).json() == {"ok": True}
    assert env.request("DELETE", f"/auth/codes/{aid}", json={"confirm": True}, headers=H).json()["error"] == "not_found"


def test_show_reveals_code_only_in_decide(env):
    aid = add(env)["added"][0]["id"]
    rid = env.post(f"/auth/codes/{aid}/show", headers=H).json()["approval_id"]
    pend = env.get("/buddy/pending", headers=H).json()["pending"][0]
    assert pend["tool"] == "auth.show" and pend["sensitive"] is True and pend["summary"] == "Show GitHub code?"
    out = decide(env, rid).json()
    from mazhost import auth_totp
    want = auth_totp.totp(b"0123456789abcdef", env.clock["t"])
    assert out["reveal"] == {"code": want, "remaining": 10, "period": 30}
    for path in (f"/buddy/state?id={rid}", "/buddy/summary", "/buddy/pending", "/buddy/nods", "/buddy/events", "/auth/codes"):
        assert want not in env.get(path, headers=H).text


def test_show_deny_has_no_reveal_and_second_allow_fails(env):
    aid = add(env)["added"][0]["id"]
    rid = env.post(f"/auth/codes/{aid}/show", headers=H).json()["approval_id"]
    assert "reveal" not in decide(env, rid, "deny").json()
    rid = env.post(f"/auth/codes/{aid}/show", headers=H).json()["approval_id"]
    assert "reveal" in decide(env, rid).json()
    assert decide(env, rid).status_code == 409


def wait_typed(c, n=1):
    for _ in range(100):
        if len(c.typed) >= n:
            return
        time.sleep(0.02)
    raise AssertionError(c.typed)


def test_fill_types_code_and_submit(env):
    from mazhost import auth_totp
    aid = add(env)["added"][0]["id"]
    rid = env.post(f"/auth/codes/{aid}/fill", json={"submit": True}, headers=H).json()["approval_id"]
    pend = env.get("/buddy/pending", headers=H).json()["pending"][0]
    assert pend["summary"] == "Type GitHub code on PC?" and pend["tool"] == "auth.fill"
    assert env.typed == []                               # nothing typed before the YES
    out = decide(env, rid).json()
    assert "reveal" not in out
    wait_typed(env, 2)
    assert env.typed == [auth_totp.totp(b"0123456789abcdef", env.clock["t"]), "<enter>"]


def test_fill_waits_for_next_period_when_under_5s(env):
    from mazhost import auth_totp
    env.clock["t"] = (1_000_000_020 // 30) * 30 + 27
    aid = add(env)["added"][0]["id"]
    rid = env.post(f"/auth/codes/{aid}/fill", headers=H).json()["approval_id"]
    decide(env, rid)
    wait_typed(env)
    assert env.typed[0] == auth_totp.totp(b"0123456789abcdef", env.clock["t"])
    assert env.clock["t"] % 30 < 5                      # slept into the next period before typing


def test_fill_unavailable(env):
    env.svc.can_type = False
    aid = add(env)["added"][0]["id"]
    r = env.post(f"/auth/codes/{aid}/fill", headers=H)
    assert r.status_code == 422 and r.json()["error"] == "fill_unavailable"
    assert env.post("/auth/codes/nope/show", headers=H).json()["error"] == "not_found"


def test_suggest_and_autosuggest(env):
    aid = add(env)["added"][0]["id"]
    assert env.get("/auth/codes/suggest", headers=H).json() == {"ok": True, "suggestion": None}
    title = {"v": "Sign in to GITHUB - Mozilla Firefox"}
    env.svc.foreground = lambda: title["v"]
    env.clock["t"] += 10
    s = env.get("/auth/codes/suggest", headers=H).json()["suggestion"]
    assert s == {"id": aid, "issuer": "GitHub", "label": "maz", "reason": "window"}
    assert env.svc.autosuggest_tick() is None            # matches issuer but no 2FA words
    title["v"] = "GitHub - Two-factor authentication"
    env.clock["t"] += 10
    first = env.svc.autosuggest_tick()
    assert first and env.get("/buddy/pending", headers=H).json()["pending"][0]["tool"] == "auth.fill"
    env.clock["t"] += 10
    assert env.svc.autosuggest_tick() is None            # same window within 2 minutes
    env.clock["t"] += 130
    assert env.svc.autosuggest_tick()
    # match words alone are enough
    env.patch(f"/auth/codes/{aid}", json={"match": ["Octocat"]}, headers=H)
    title["v"] = "octocat dashboard"
    env.clock["t"] += 10
    assert env.svc.autosuggest_tick()
    assert env.put("/auth/prefs", json={"autosuggest": False}, headers=H).json()["autosuggest"] is False
    title["v"] = "octocat other"
    env.clock["t"] += 10
    assert env.svc.autosuggest_tick() is None


def test_prefs_defaults(env):
    assert env.get("/auth/prefs", headers=H).json() == {"ok": True, "autosuggest": True, "require_key_for_show": False}


# ---- Piblo Key
def enrol_string():
    keys = pk.card_keys(MASTER, UID)
    return pk.format_enrol_code(UID, keys["meta"], keys["file"]), keys


def sun_p(keys, ctr, uid=UID):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    plain = b"\xc7" + uid + ctr.to_bytes(3, "little") + bytes(5)
    p = Cipher(algorithms.AES(keys["meta"]), modes.CBC(bytes(16))).encryptor().update(plain)
    return p.hex().upper(), pk.sun_mac(keys["file"], uid, ctr).hex().upper()


def test_key_enrol_tap_replay(env):
    code, keys = enrol_string()
    r = env.post("/auth/key/enroll", json={"code": code}, headers=H).json()
    assert r["ok"] and r["key"]["uid_tail"] == "EE01" and r["key"]["name"] == "Piblo Key 1"
    assert env.post("/auth/key/enroll", json={"code": code}, headers=H).json()["error"] == "duplicate"
    assert "last_tap_at" in env.get("/auth/key", headers=H).json()["keys"][0]
    assert code.split("-")[2] not in env.get("/auth/key", headers=H).text
    p, c = sun_p(keys, 5)
    ok = env.post("/auth/key/tap", json={"url": f"https://piblo.co.uk/k?p={p}&c={c}"}, headers=H).json()
    assert ok["ok"] and ok["fresh"] and ok["key_id"] == r["key"]["id"]
    assert env.post("/auth/key/tap", json={"p": p, "c": c}, headers=H).json()["error"] == "replay"
    p3, c3 = sun_p(keys, 4)
    assert env.post("/auth/key/tap", json={"p": p3, "c": c3}, headers=H).json()["error"] == "replay"
    p6, c6 = sun_p(keys, 6)
    assert env.post("/auth/key/tap", json={"p": p6, "c": "00" * 8}, headers=H).json()["error"] == "bad_mac"
    assert env.post("/auth/key/tap", json={"p": p6, "c": c6}, headers=H).status_code == 200
    stranger = sun_p(pk.card_keys(bytes(16), UID), 9)
    assert env.post("/auth/key/tap", json={"p": stranger[0], "c": stranger[1]}, headers=H).json()["error"] == "unknown_key"


def test_replay_counter_survives_restart(env, tmp_path):
    code, keys = enrol_string()
    env.post("/auth/key/enroll", json={"code": code}, headers=H)
    p, c = sun_p(keys, 7)
    env.post("/auth/key/tap", json={"p": p, "c": c}, headers=H)
    app2 = create_app(Settings(token=TOKEN, control_dir=str(tmp_path / "control"), _env_file=None))
    assert TestClient(app2).post("/auth/key/tap", json={"p": p, "c": c}, headers=H).json()["error"] == "replay"


def test_max_three_keys_and_delete(env):
    ids = []
    for n in range(3):
        uid = bytes.fromhex(f"04AABBCCDDEE0{n}")
        k = pk.card_keys(MASTER, uid)
        ids.append(env.post("/auth/key/enroll", json={"code": pk.format_enrol_code(uid, k["meta"], k["file"])}, headers=H).json()["key"]["id"])
    uid = bytes.fromhex("04AABBCCDDEE09")
    k = pk.card_keys(MASTER, uid)
    assert env.post("/auth/key/enroll", json={"code": pk.format_enrol_code(uid, k["meta"], k["file"])}, headers=H).json()["error"] == "too_many_keys"
    assert env.request("DELETE", f"/auth/key/{ids[0]}", headers=H).json()["error"] == "not_confirmed"
    assert env.request("DELETE", f"/auth/key/{ids[0]}", json={"confirm": True}, headers=H).json() == {"ok": True}
    assert len(env.get("/auth/key", headers=H).json()["keys"]) == 2


def test_require_key_gate(env):
    code, keys = enrol_string()
    env.post("/auth/key/enroll", json={"code": code}, headers=H)
    # Raw Buddy approval with require_key
    rid = env.post("/buddy/request", json={"tool": "Bash", "summary": "rm", "require_key": True}, headers=H).json()["id"]
    assert env.get("/buddy/summary", headers=H).json()["items"][0]["require_key"] is True
    r = decide(env, rid)
    assert r.status_code == 409 and r.json()["error"] == "key_tap_required"
    assert env.get(f"/buddy/state?id={rid}", headers=H).json()["decision"] is None   # still pending
    time.sleep(0.01)
    p, c = sun_p(keys, 1)
    env.post("/auth/key/tap", json={"p": p, "c": c}, headers=H)
    # the test clock is frozen: the proof is not older than the request (t >= since) -> allowed
    env.svc.clock = lambda: time.time()
    p2, c2 = sun_p(keys, 2)
    env.post("/auth/key/tap", json={"p": p2, "c": c2}, headers=H)
    assert decide(env, rid).json()["decision"] == "allow"


def test_tap_proof_expires_and_must_be_newer(env):
    code, keys = enrol_string()
    env.post("/auth/key/enroll", json={"code": code}, headers=H)
    env.svc.clock = lambda: time.time()
    p, c = sun_p(keys, 1)
    env.post("/auth/key/tap", json={"p": p, "c": c}, headers=H)       # tap BEFORE the request
    time.sleep(0.02)
    rid = env.post("/buddy/request", json={"tool": "x", "require_key": True}, headers=H).json()["id"]
    assert decide(env, rid).status_code == 409                         # older tap does not count
    now = time.time()
    env.svc.clock = lambda: now + 61
    p2, c2 = sun_p(keys, 2)
    env.post("/auth/key/tap", json={"p": p2, "c": c2}, headers=H)
    env.svc.clock = lambda: now + 122                                  # proof is 61 s old
    assert decide(env, rid).status_code == 409


def test_show_requires_key_pref(env):
    code, keys = enrol_string()
    env.post("/auth/key/enroll", json={"code": code}, headers=H)
    env.svc.clock = lambda: time.time()
    aid = add(env)["added"][0]["id"]
    env.put("/auth/prefs", json={"require_key_for_show": True}, headers=H)
    rid = env.post(f"/auth/codes/{aid}/show", headers=H).json()["approval_id"]
    assert decide(env, rid).status_code == 409
    time.sleep(0.01)
    p, c = sun_p(keys, 3)
    env.post("/auth/key/tap", json={"p": p, "c": c}, headers=H)
    assert "reveal" in decide(env, rid).json()


def test_require_key_without_auth_fails_closed():
    from mazhost.buddy import Buddy
    b = Buddy()
    rid = b.request("x", "y", require_key=True)["id"]
    with pytest.raises(RuntimeError, match="key_tap_required"):
        b.decide(rid, "allow")
    # unchanged default behaviour
    assert b.decide(b.request("x", "y")["id"], "allow")["decision"] == "allow"
