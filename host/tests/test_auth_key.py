import pytest

from mazhost import piblo_key as pk
from mazhost.auth_totp import AuthError

ZERO = bytes(16)
# NXP AN12196 SUN example (K_meta = K_file = 0). Verified by computing it here, not just copied.
P = bytes.fromhex("EF963FF7828658A599F3041510671E88")
C = bytes.fromhex("94EED9EE65337086")
UID = bytes.fromhex("04DE5F1EACC040")


def test_an12196_vector():
    uid, ctr = pk.decrypt_picc(ZERO, P)
    assert uid == UID and ctr == int.from_bytes(bytes.fromhex("3D0000"), "little") == 61
    assert pk.sun_mac(ZERO, uid, ctr) == C
    assert pk.verify_sun(P, C, ZERO, ZERO) == (UID, 61)


def test_bad_mac_and_wrong_key():
    with pytest.raises(ValueError, match="bad_mac"):
        pk.verify_sun(P, bytes(8), ZERO, ZERO)
    with pytest.raises(ValueError):
        pk.verify_sun(P, C, bytes([1]) * 16, ZERO)


def test_diversification_is_per_uid_role_and_stable():
    master = bytes(range(16))
    a, b = pk.card_keys(master, UID), pk.card_keys(master, bytes.fromhex("04DE5F1EACC041"))
    assert len({a["app"], a["meta"], a["file"], b["file"]}) == 4
    assert a["meta"] == b["meta"] == pk.meta_key(master)  # shared: the UID is encrypted under it
    assert a["file"] == pk.aes_cmac(master, b"\x02" + UID + b"piblo-key-v1")
    assert a["file"] == pk.aes_cmac(master, b"\x02" + UID + b"piblo-key-v1")
    assert pk.card_keys(master, UID) == a and all(len(k) == 16 for k in a.values())


def test_enrol_code_roundtrip_hex_and_base32():
    import base64
    code = pk.format_enrol_code(UID, bytes(range(16)), bytes(range(16, 32)))
    assert code.startswith("PK1-04DE5F1EACC040-") and len(code.split("-")) == 4
    assert pk.parse_enrol_code(code) == (UID, bytes(range(16)), bytes(range(16, 32)))
    packed = base64.b32encode(UID + bytes(range(32))).decode().rstrip("=")
    assert pk.parse_enrol_code("PK1-" + packed) == (UID, bytes(range(16)), bytes(range(16, 32)))
    for bad in ("", "PK2-x", "PK1-04DE5F1EACC040-00-00", "PK1-" + "Z" * 14 + "-" + "0" * 32 + "-" + "0" * 32):
        with pytest.raises(AuthError):
            pk.parse_enrol_code(bad)


def test_parse_tap():
    url = f"https://piblo.co.uk/k?p={P.hex().upper()}&c={C.hex()}"
    assert pk.parse_tap(url=url) == (P, C)
    assert pk.parse_tap(p=P.hex(), c=C.hex()) == (P, C)
    for bad in (f"http://piblo.co.uk/k?p={P.hex()}&c={C.hex()}", f"https://evil.example/k?p={P.hex()}&c={C.hex()}",
                "https://piblo.co.uk/k?p=zz&c=00", "https://piblo.co.uk/other?p=" + P.hex() + "&c=" + C.hex()):
        with pytest.raises(AuthError):
            pk.parse_tap(url=bad)
