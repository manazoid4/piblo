"""Encrypted vault for Piblo Keys: authenticator secrets, Piblo Key card keys, prefs.

One AES-256-GCM file (<dir>/vault.json). The data key is wrapped with Windows DPAPI (current user) on
Windows, otherwise kept in a 0600 file beside the vault. Nothing here logs or raises with secret values."""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .auth_totp import AuthError

_AAD = b"piblo-vault-v1"
_DPAPI_MAGIC = b"PBDPAPI1"
_DEFAULT = {"v": 1, "accounts": [], "keys": [], "prefs": {"autosuggest": True, "require_key_for_show": False}}


class VaultError(RuntimeError):
    pass


def _write_private(path: Path, data: bytes) -> None:
    """Atomic replace with 0600 from the first byte (no world-readable window)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{secrets.token_hex(4)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    source = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source), "Piblo Keys", None, None, None, flags, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(out))
    if not ok:
        raise VaultError("dpapi_failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


class Vault:
    def __init__(self, directory: Path, use_dpapi: bool | None = None) -> None:
        self.dir = Path(directory)
        self.path = self.dir / "vault.json"
        self.key_path = self.dir / "vault.key"
        self.use_dpapi = sys.platform == "win32" if use_dpapi is None else use_dpapi
        self._lock = threading.RLock()
        self._key: bytes | None = None

    # ---- data key
    def _data_key(self) -> bytes:
        if self._key:
            return self._key
        if self.key_path.exists():
            blob = self.key_path.read_bytes()
            if blob.startswith(_DPAPI_MAGIC):
                if not self.use_dpapi:
                    raise VaultError("vault_key_needs_windows")
                key = _dpapi(blob[len(_DPAPI_MAGIC):], protect=False)
            else:
                key = blob
        else:
            key = secrets.token_bytes(32)
            _write_private(self.key_path, _DPAPI_MAGIC + _dpapi(key, protect=True) if self.use_dpapi else key)
        if len(key) != 32:
            raise VaultError("vault_key_invalid")
        self._key = key
        return key

    # ---- document
    def _load(self) -> dict:
        if not self.path.exists():
            return json.loads(json.dumps(_DEFAULT))
        try:
            wrapper = json.loads(self.path.read_text(encoding="utf-8"))
            nonce, blob = base64.b64decode(wrapper["n"]), base64.b64decode(wrapper["c"])
            doc = json.loads(AESGCM(self._data_key()).decrypt(nonce, blob, _AAD))
        except (OSError, ValueError, KeyError, InvalidTag):
            raise VaultError("vault_unreadable") from None
        for name, default in _DEFAULT.items():
            doc.setdefault(name, json.loads(json.dumps(default)))
        return doc

    def _save(self, doc: dict) -> None:
        nonce = secrets.token_bytes(12)
        blob = AESGCM(self._data_key()).encrypt(nonce, json.dumps(doc, separators=(",", ":")).encode(), _AAD)
        wrapper = {"v": 1, "n": base64.b64encode(nonce).decode(), "c": base64.b64encode(blob).decode()}
        _write_private(self.path, json.dumps(wrapper).encode())

    def read(self) -> dict:
        with self._lock:
            return self._load()

    def update(self, fn):
        """Load, apply fn(doc) -> result, save atomically. fn may raise to abort without saving."""
        with self._lock:
            doc = self._load()
            result = fn(doc)
            self._save(doc)
            return result

    # ---- accounts
    @staticmethod
    def public_account(rec: dict) -> dict:
        return {"id": rec["id"], "issuer": rec["issuer"], "label": rec["label"], "digits": rec["digits"],
                "period": rec["period"], "algorithm": rec["algorithm"], "match": list(rec.get("match", [])),
                "added_at": rec["added_at"]}

    def add_accounts(self, parsed: list[dict]) -> tuple[list[dict], list[dict]]:
        def apply(doc):
            added, skipped = [], []
            for item in parsed:
                secret = base64.b32encode(item["secret"]).decode()
                if any(a["issuer"] == item["issuer"] and a["label"] == item["label"] and a["secret"] == secret
                       for a in doc["accounts"]):
                    skipped.append({"label": _shown(item), "reason": "duplicate"})
                    continue
                rec = {"id": secrets.token_hex(4), "issuer": item["issuer"], "label": item["label"], "secret": secret,
                       "digits": item["digits"], "period": item["period"], "algorithm": item["algorithm"],
                       "match": [], "added_at": int(time.time())}
                doc["accounts"].append(rec)
                added.append(self.public_account(rec))
            return added, skipped
        return self.update(apply)

    def accounts(self) -> list[dict]:
        return [self.public_account(a) for a in self.read()["accounts"]]

    def account_secret(self, account_id: str) -> tuple[dict, bytes]:
        for rec in self.read()["accounts"]:
            if rec["id"] == account_id:
                pad = "=" * (-len(rec["secret"]) % 8)
                return self.public_account(rec), base64.b32decode(rec["secret"] + pad)
        raise AuthError("not_found", "No such account.")

    def patch_account(self, account_id: str, issuer: str | None, label: str | None, match: list[str] | None) -> dict:
        def apply(doc):
            for rec in doc["accounts"]:
                if rec["id"] == account_id:
                    if issuer is not None:
                        rec["issuer"] = issuer
                    if label is not None:
                        rec["label"] = label
                    if match is not None:
                        rec["match"] = match
                    return self.public_account(rec)
            raise AuthError("not_found", "No such account.")
        return self.update(apply)

    def delete_account(self, account_id: str) -> None:
        def apply(doc):
            before = len(doc["accounts"])
            doc["accounts"] = [a for a in doc["accounts"] if a["id"] != account_id]
            if len(doc["accounts"]) == before:
                raise AuthError("not_found", "No such account.")
        self.update(apply)

    # ---- prefs
    def prefs(self) -> dict:
        return dict(self.read()["prefs"])

    def set_prefs(self, **changes) -> dict:
        def apply(doc):
            doc["prefs"].update({k: bool(v) for k, v in changes.items() if v is not None})
            return dict(doc["prefs"])
        return self.update(apply)


def _shown(item: dict) -> str:
    return f"{item['issuer']}: {item['label']}" if item["issuer"] and item["label"] else (item["label"] or item["issuer"] or "account")
