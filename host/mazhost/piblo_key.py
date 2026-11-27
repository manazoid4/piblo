"""Piblo Key: NTAG 424 DNA key diversification and SUN (Secure Unique NFC) verification.

Follows docs/protocol/AUTH.md and NXP AN12196. Pure functions plus the URL/enrolment parsers."""
from __future__ import annotations

import base64
import binascii
import hmac
import re
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.cmac import CMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .auth_totp import AuthError

DIVERSIFY_TAG = b"piblo-key-v1"
ROLE_APP, ROLE_META, ROLE_FILE = 0x00, 0x01, 0x02
TAP_HOSTS = ("piblo.co.uk", "www.piblo.co.uk")
MAX_KEYS = 3
_HEX = re.compile(r"^[0-9a-fA-F]+$")


def aes_cmac(key: bytes, message: bytes) -> bytes:
    mac = CMAC(algorithms.AES(key))
    mac.update(message)
    return mac.finalize()


def diversify(master: bytes, uid: bytes, role: int) -> bytes:
    if len(master) != 16 or len(uid) != 7:
        raise ValueError("master must be 16 bytes and uid 7 bytes")
    return aes_cmac(master, bytes([role]) + uid + DIVERSIFY_TAG)


def meta_key(master: bytes) -> bytes:
    """Shared SDM meta-read key (same on every card). The UID sits inside the encrypted PICCData, so
    a verifier must decrypt it before it can derive anything per card (NXP AN12196 approach). Knowing
    this key only reveals a tap's UID and counter; forging a tap still needs the per-card file key."""
    if len(master) != 16:
        raise ValueError("master must be 16 bytes")
    return aes_cmac(master, bytes([ROLE_META]) + bytes(7) + DIVERSIFY_TAG)


def card_keys(master: bytes, uid: bytes) -> dict[str, bytes]:
    return {"app": diversify(master, uid, ROLE_APP), "meta": meta_key(master),
            "file": diversify(master, uid, ROLE_FILE)}


def decrypt_picc(k_meta: bytes, p: bytes) -> tuple[bytes, int]:
    """AES-128-CBC (IV 0) decrypt of PICCData -> (uid, read counter). Raises ValueError if not SUN data."""
    if len(p) != 16:
        raise ValueError("bad_p")
    plain = Cipher(algorithms.AES(k_meta), modes.CBC(bytes(16))).decryptor().update(p)
    if plain[0] != 0xC7:                      # UID mirrored + counter mirrored + 7-byte UID
        raise ValueError("bad_picc")
    return plain[1:8], int.from_bytes(plain[8:11], "little")


def sun_mac(k_file: bytes, uid: bytes, ctr: int) -> bytes:
    sv2 = bytes.fromhex("3CC300010080") + uid + ctr.to_bytes(3, "little")
    session = aes_cmac(k_file, sv2)
    full = aes_cmac(session, b"")
    return bytes(full[i] for i in range(1, 16, 2))


def verify_sun(p: bytes, c: bytes, k_meta: bytes, k_file: bytes) -> tuple[bytes, int]:
    """Return (uid, counter) when the tap is genuine for these keys, else raise ValueError('bad_mac'/...)."""
    uid, ctr = decrypt_picc(k_meta, p)
    if not hmac.compare_digest(sun_mac(k_file, uid, ctr), c):
        raise ValueError("bad_mac")
    return uid, ctr


def parse_tap(url: str | None = None, p: str | None = None, c: str | None = None) -> tuple[bytes, bytes]:
    if url:
        parts = urlsplit(url.strip())
        if parts.scheme != "https" or (parts.hostname or "").lower() not in TAP_HOSTS or parts.path.rstrip("/") != "/k":
            raise AuthError("unknown_key", "That link is not a Piblo Key tap.")
        query = parse_qs(parts.query)
        p, c = query.get("p", [""])[0], query.get("c", [""])[0]
    if not (p and c and len(p) == 32 and len(c) == 16 and _HEX.match(p) and _HEX.match(c)):
        raise AuthError("unknown_key", "That is not a valid Piblo Key tap.")
    return bytes.fromhex(p), bytes.fromhex(c)


def format_enrol_code(uid: bytes, k_meta: bytes, k_file: bytes) -> str:
    return f"PK1-{uid.hex().upper()}-{k_meta.hex().upper()}-{k_file.hex().upper()}"


def parse_enrol_code(code: str) -> tuple[bytes, bytes, bytes]:
    """PK1-<uid 14 hex>-<32 hex>-<32 hex>, or PK1- plus the 39 raw bytes in base32."""
    text = code.strip()
    if not text.upper().startswith("PK1-"):
        raise AuthError("invalid_code", "That is not a Piblo Key code.")
    body = text[4:]
    pieces = body.split("-")
    try:
        if len(pieces) == 3 and [len(x) for x in pieces] == [14, 32, 32] and all(_HEX.match(x) for x in pieces):
            uid, k_meta, k_file = (bytes.fromhex(x) for x in pieces)
        else:
            packed = "".join(body.split()).replace("-", "").upper()
            raw = base64.b32decode(packed + "=" * (-len(packed) % 8))
            if len(raw) != 39:
                raise ValueError
            uid, k_meta, k_file = raw[:7], raw[7:23], raw[23:]
    except (ValueError, binascii.Error):
        raise AuthError("invalid_code", "That is not a valid Piblo Key code.") from None
    return uid, k_meta, k_file
