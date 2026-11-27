"""TOTP (RFC 6238) and otpauth:// / otpauth-migration:// parsing for Piblo Keys. Pure functions, no I/O."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
import time
from urllib.parse import parse_qs, unquote, urlsplit

ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}
MAX_MIGRATION_BYTES = 256 * 1024
MAX_ACCOUNTS_PER_IMPORT = 500


class AuthError(Exception):
    """Carries a stable machine code; the message never contains a secret."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def hotp(secret: bytes, counter: int, digits: int = 6, algorithm: str = "SHA1") -> str:
    digest = hmac.new(secret, struct.pack(">Q", counter), ALGORITHMS[algorithm]).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def totp(secret: bytes, at: float | None = None, digits: int = 6, period: int = 30, algorithm: str = "SHA1") -> str:
    return hotp(secret, int((time.time() if at is None else at) // period), digits, algorithm)


def remaining(period: int = 30, at: float | None = None) -> float:
    now = time.time() if at is None else at
    return period - (now % period)


def decode_secret(text: str) -> bytes:
    cleaned = "".join(text.split()).replace("-", "").upper().rstrip("=")
    if not cleaned:
        raise AuthError("invalid_secret", "The secret is empty.")
    try:
        secret = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (binascii.Error, ValueError):
        raise AuthError("invalid_secret", "The secret is not valid base32.") from None
    if not 10 <= len(secret) <= 128:
        raise AuthError("invalid_secret", "The secret has an unusual length.")
    return secret


def _split_label(label: str, issuer: str) -> tuple[str, str]:
    prefix, sep, rest = label.partition(":")
    if sep:
        if not issuer:
            issuer = prefix.strip()
        if prefix.strip().lower() == issuer.lower():
            label = rest
    return issuer.strip(), label.strip()


def parse_otpauth(uri: str) -> dict:
    """otpauth://totp/Issuer:label?secret=...  ->  account fields incl. raw secret bytes."""
    parts = urlsplit(uri.strip())
    if parts.scheme.lower() != "otpauth":
        raise AuthError("invalid_uri", "Not an otpauth link.")
    kind = parts.netloc.lower()
    if kind == "hotp":
        raise AuthError("unsupported_type", "Counter-based (HOTP) codes are not supported.")
    if kind != "totp":
        raise AuthError("invalid_uri", "Unknown otpauth type.")
    query = {k.lower(): v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    secret = decode_secret(query.get("secret", ""))
    algorithm = query.get("algorithm", "SHA1").upper().replace("-", "")
    if algorithm not in ALGORITHMS:
        raise AuthError("invalid_uri", "Unsupported algorithm.")
    try:
        digits = int(query.get("digits", "6"))
        period = int(query.get("period", "30"))
    except ValueError:
        raise AuthError("invalid_uri", "Digits and period must be numbers.") from None
    if digits not in (6, 7, 8) or not 1 <= period <= 300:
        raise AuthError("invalid_uri", "Unsupported digits or period.")
    issuer, label = _split_label(unquote(parts.path.lstrip("/")), query.get("issuer", ""))
    return {"issuer": issuer, "label": label, "secret": secret, "digits": digits,
            "period": period, "algorithm": algorithm}


# ---- Google Authenticator export: protobuf by hand --------------------------------

def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf) or shift > 63:
            raise AuthError("invalid_uri", "Truncated export data.")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, wire_type, value) where value is int or bytes."""
    pos = 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 2:
            size, pos = _varint(buf, pos)
            if pos + size > len(buf):
                raise AuthError("invalid_uri", "Truncated export data.")
            value, pos = buf[pos:pos + size], pos + size
        elif wire == 1:
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire == 5:
            value, pos = buf[pos:pos + 4], pos + 4
        else:
            raise AuthError("invalid_uri", "Unsupported export data.")
        yield number, wire, value


_MIG_ALGO = {0: "SHA1", 1: "SHA1", 2: "SHA256", 3: "SHA512"}   # 4 = MD5, unsupported
_MIG_DIGITS = {0: 6, 1: 6, 2: 8}


def parse_migration(uri: str) -> tuple[list[dict], list[dict]]:
    """otpauth-migration://offline?data=...  ->  (accounts, skipped[{label, reason}])."""
    parts = urlsplit(uri.strip())
    if parts.scheme.lower() != "otpauth-migration":
        raise AuthError("invalid_uri", "Not a Google Authenticator export link.")
    raw = parse_qs(parts.query).get("data", [""])[0].replace(" ", "+")
    if not raw or len(raw) > MAX_MIGRATION_BYTES * 2:
        raise AuthError("invalid_uri", "The export link has no usable data.")
    try:
        payload = base64.b64decode(raw.replace("-", "+").replace("_", "/") + "=" * (-len(raw) % 4), validate=True)
    except (binascii.Error, ValueError):
        raise AuthError("invalid_uri", "The export data is not valid.") from None
    accounts: list[dict] = []
    skipped: list[dict] = []
    for number, wire, value in _fields(payload):
        if number != 1 or wire != 2:
            continue
        if len(accounts) + len(skipped) >= MAX_ACCOUNTS_PER_IMPORT:
            break
        secret, name, issuer, algo, digits, kind = b"", "", "", 1, 1, 2
        for n, w, v in _fields(value):
            if n == 1 and w == 2:
                secret = v
            elif n == 2 and w == 2:
                name = v.decode("utf-8", "replace")
            elif n == 3 and w == 2:
                issuer = v.decode("utf-8", "replace")
            elif n == 4 and w == 0:
                algo = v
            elif n == 5 and w == 0:
                digits = v
            elif n == 6 and w == 0:
                kind = v
        issuer, label = _split_label(name, issuer)
        shown = f"{issuer}: {label}" if issuer and label else (label or issuer or "account")
        if kind == 1:
            skipped.append({"label": shown, "reason": "unsupported_type"})
        elif algo not in _MIG_ALGO or digits not in _MIG_DIGITS:
            skipped.append({"label": shown, "reason": "unsupported_algorithm"})
        elif not 10 <= len(secret) <= 128:
            skipped.append({"label": shown, "reason": "invalid_secret"})
        else:
            accounts.append({"issuer": issuer, "label": label, "secret": bytes(secret),
                             "digits": _MIG_DIGITS[digits], "period": 30, "algorithm": _MIG_ALGO[algo]})
    if not accounts and not skipped:
        raise AuthError("invalid_uri", "The export link has no accounts.")
    return accounts, skipped


def parse_uri(uri: str) -> tuple[list[dict], list[dict]]:
    scheme = uri.strip().split(":", 1)[0].lower()
    if scheme == "otpauth-migration":
        return parse_migration(uri)
    if scheme == "otpauth":
        return [parse_otpauth(uri)], []
    raise AuthError("invalid_uri", "Expected an otpauth:// or otpauth-migration:// link.")
