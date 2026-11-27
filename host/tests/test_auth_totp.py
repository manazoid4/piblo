import base64
from urllib.parse import quote

import pytest

from mazhost import auth_totp
from mazhost.auth_totp import AuthError

SEEDS = {"SHA1": b"12345678901234567890", "SHA256": b"12345678901234567890123456789012",
         "SHA512": b"1234567890123456789012345678901234567890123456789012345678901234"}
# RFC 6238 appendix B
VECTORS = [
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


@pytest.mark.parametrize("at,sha1,sha256,sha512", VECTORS)
def test_rfc6238_vectors(at, sha1, sha256, sha512):
    for algo, want in (("SHA1", sha1), ("SHA256", sha256), ("SHA512", sha512)):
        assert auth_totp.totp(SEEDS[algo], at, 8, 30, algo) == want


def test_six_digits_is_truncation_of_same_hotp():
    assert auth_totp.totp(SEEDS["SHA1"], 59, 6) == "287082"


def test_remaining():
    assert auth_totp.remaining(30, 59) == 1
    assert auth_totp.remaining(30, 60) == 30


def b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode()


def test_otpauth_issuer_param_and_padding_and_spaces():
    secret = b"0123456789abcdef"
    text = b32(secret).rstrip("=").lower()
    spaced = " ".join(text[i:i + 4] for i in range(0, len(text), 4))
    uri = f"otpauth://totp/GitHub:maz@example.com?secret={quote(spaced)}&issuer=GitHub&digits=8&period=60&algorithm=SHA256"
    a = auth_totp.parse_otpauth(uri)
    assert (a["issuer"], a["label"], a["digits"], a["period"], a["algorithm"]) == ("GitHub", "maz@example.com", 8, 60, "SHA256")
    assert a["secret"] == secret


def test_otpauth_issuer_from_label_prefix():
    a = auth_totp.parse_otpauth(f"otpauth://totp/Acme%20Co:bob?secret={b32(b'abcdefghij')}")
    assert (a["issuer"], a["label"], a["digits"], a["period"], a["algorithm"]) == ("Acme Co", "bob", 6, 30, "SHA1")


def test_otpauth_errors():
    with pytest.raises(AuthError) as e:
        auth_totp.parse_otpauth("otpauth://hotp/x?secret=JBSWY3DPEHPK3PXP&counter=1")
    assert e.value.code == "unsupported_type"
    for bad in ("otpauth://totp/x?secret=!!!!", "otpauth://totp/x", "otpauth://totp/x?secret=AAAA"):
        with pytest.raises(AuthError) as e:
            auth_totp.parse_otpauth(bad)
        assert e.value.code == "invalid_secret"
    with pytest.raises(AuthError) as e:
        auth_totp.parse_uri("https://example.com")
    assert e.value.code == "invalid_uri"


# ---- protobuf builder for the Google Authenticator export
def varint(n: int) -> bytes:
    out = b""
    while True:
        byte, n = n & 0x7F, n >> 7
        out += bytes([byte | (0x80 if n else 0)])
        if not n:
            return out


def ld(field: int, data: bytes) -> bytes:
    return varint(field << 3 | 2) + varint(len(data)) + data


def vi(field: int, value: int) -> bytes:
    return varint(field << 3) + varint(value)


def otp(secret: bytes, name: str, issuer: str, algo: int, digits: int, kind: int) -> bytes:
    return ld(1, ld(1, secret) + ld(2, name.encode()) + ld(3, issuer.encode()) + vi(4, algo) + vi(5, digits) + vi(6, kind))


def migration_uri(payload: bytes, urlsafe=False) -> str:
    raw = base64.b64encode(payload).decode()
    return "otpauth-migration://offline?data=" + quote(raw, safe="")


def test_migration_payload():
    payload = (otp(b"A" * 10, "GitHub:maz", "GitHub", 1, 1, 2)
               + otp(b"B" * 20, "alice@x.io", "Example", 3, 2, 2)
               + otp(b"C" * 10, "counter", "Old", 1, 1, 1)           # HOTP -> skipped
               + otp(b"D" * 10, "md5", "Legacy", 4, 1, 2)            # MD5 -> skipped
               + vi(2, 1) + vi(3, 1) + vi(4, 7))                     # version/batch fields are ignored
    accounts, skipped = auth_totp.parse_uri(migration_uri(payload))
    assert [(a["issuer"], a["label"], a["algorithm"], a["digits"]) for a in accounts] == [
        ("GitHub", "maz", "SHA1", 6), ("Example", "alice@x.io", "SHA512", 8)]
    assert accounts[0]["secret"] == b"A" * 10 and accounts[0]["period"] == 30
    assert [(s["label"], s["reason"]) for s in skipped] == [
        ("Old: counter", "unsupported_type"), ("Legacy: md5", "unsupported_algorithm")]


def test_migration_tolerates_plus_as_space_and_rejects_garbage():
    payload = otp(b"\xfb\xff\xfe" * 4, "n", "I", 1, 1, 2)
    uri = "otpauth-migration://offline?data=" + base64.b64encode(payload).decode()   # raw '+' becomes a space in the query
    assert auth_totp.parse_uri(uri)[0][0]["secret"] == b"\xfb\xff\xfe" * 4
    for bad in ("otpauth-migration://offline?data=", "otpauth-migration://offline?data=%%%",
                migration_uri(b"\x0a\x7f\x01")):
        with pytest.raises(AuthError) as e:
            auth_totp.parse_uri(bad)
        assert e.value.code == "invalid_uri"
