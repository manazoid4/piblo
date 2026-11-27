"""Piblo Keys routes: authenticator codes (typed only after a physical YES) and the Piblo Key card.

Contract: docs/protocol/AUTH.md. No route here returns a secret or a code except the one-off `reveal`
that Buddy.decide adds to the response that allowed an `auth.show` approval."""
from __future__ import annotations

import logging
import secrets
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import auth_totp, piblo_key
from .auth_totp import AuthError
from .auth_vault import Vault, VaultError

log = logging.getLogger("piblo.auth")

PROOF_TTL_S = 60.0
FILL_MIN_REMAINING_S = 5.0
SUGGEST_CACHE_S = 2.0
SUGGEST_COOLDOWN_S = 120.0
TWOFA_WORDS = ("two-factor", "two factor", "2fa", "verification", "authenticator", "one-time", "security code", "verify")
MAX_PENDING = 64


def _foreground_title() -> str | None:
    if sys.platform != "win32":
        return None
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value or None


def _type_text(text: str) -> None:
    from .flow_routes import type_text
    type_text(text)


def _press_enter() -> None:
    from .flow_routes import _tap
    _tap(0x0D)


class AuthService:
    def __init__(self, directory: Path, buddy, *, foreground: Callable[[], str | None] | None = None,
                 typer: Callable[[str], None] | None = None, enter: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 can_type: bool | None = None, vault: Vault | None = None) -> None:
        self.vault = vault or Vault(directory)
        self.buddy = buddy
        self.foreground = foreground or _foreground_title
        self.typer = typer or _type_text
        self.enter = enter or _press_enter
        self.clock, self.sleep = clock, sleep
        self.can_type = (sys.platform == "win32") if can_type is None else can_type
        self._lock = threading.Lock()
        self._pending: OrderedDict[str, dict] = OrderedDict()     # approval id -> {account_id, kind, submit}
        self._proofs: list[tuple[str, float]] = []
        self._title: tuple[float, str | None] = (-1e9, None)
        self._shown: dict[str, float] = {}                        # window title -> last autosuggest time
        self._stop = threading.Event()
        buddy.key_proof_check = self.key_proof_ok
        buddy.allow_hooks["auth.fill"] = self._on_allow
        buddy.allow_hooks["auth.show"] = self._on_allow

    # ---- codes
    def import_uri(self, uri: str) -> dict:
        parsed, skipped = auth_totp.parse_uri(uri)
        added, dup = self.vault.add_accounts(parsed)
        return {"ok": True, "added": added, "skipped": skipped + dup}

    def patch(self, account_id: str, issuer, label, match) -> dict:
        match = None if match is None else [w.strip()[:60] for w in match if w.strip()][:10]
        return {"ok": True, "account": self.vault.patch_account(
            account_id, None if issuer is None else issuer.strip()[:80], None if label is None else label.strip()[:120], match)}

    def _raise(self, tool: str, account: dict, submit: bool = False) -> dict:
        if tool == "auth.fill":
            if not self.can_type:
                raise AuthError("fill_unavailable", "Typing codes needs Windows with a window in focus.")
            if self.foreground() is None and sys.platform == "win32":
                raise AuthError("fill_unavailable", "No window is in focus to type into.")
        name = account["issuer"] or account["label"] or "account"
        verb = "Type" if tool == "auth.fill" else "Show"
        key_needed = tool == "auth.show" and bool(self.vault.prefs().get("require_key_for_show"))
        approval = self.buddy.request(tool, f"{verb} {name[:60]} code{' on PC' if tool == 'auth.fill' else ''}?",
                                      agent="Piblo", sensitive=True, require_key=key_needed)
        with self._lock:
            self._pending[approval["id"]] = {"account_id": account["id"], "kind": tool, "submit": submit}
            while len(self._pending) > MAX_PENDING:
                self._pending.popitem(last=False)
        return {"ok": True, "approval_id": approval["id"]}

    def request(self, account_id: str, tool: str, submit: bool = False) -> dict:
        account, _ = self.vault.account_secret(account_id)
        return self._raise(tool, account, submit)

    def current(self, account_id: str) -> dict:
        account, secret = self.vault.account_secret(account_id)
        now = self.clock()
        return {"code": auth_totp.totp(secret, now, account["digits"], account["period"], account["algorithm"]),
                "remaining": int(auth_totp.remaining(account["period"], now)), "period": account["period"]}

    def _on_allow(self, item: dict) -> dict | None:
        """Buddy.decide hook. Runs once, only for an allowed auth.* approval."""
        with self._lock:
            info = self._pending.pop(item["id"], None)
        if not info:
            return None
        try:
            if info["kind"] == "auth.show":
                return {"reveal": self.current(info["account_id"])}
            threading.Thread(target=self._fill, args=(info["account_id"], info["submit"]), daemon=True).start()
        except (AuthError, VaultError) as error:
            log.warning("auth hook failed: %s", getattr(error, "code", type(error).__name__))
        return None

    def _fill(self, account_id: str, submit: bool) -> None:
        try:
            account, secret = self.vault.account_secret(account_id)
            period = account["period"]
            left = auth_totp.remaining(period, self.clock())
            if left < FILL_MIN_REMAINING_S:
                self.sleep(left + 0.3)
            code = auth_totp.totp(secret, self.clock(), account["digits"], period, account["algorithm"])
            self.typer(code)
            if submit:
                self.enter()
        except Exception as error:   # never include the message: it could echo typed text on some platforms
            log.warning("auth fill failed: %s", type(error).__name__)

    # ---- suggest
    def _title_now(self) -> str | None:
        now = self.clock()
        if now - self._title[0] >= SUGGEST_CACHE_S:
            try:
                self._title = (now, self.foreground())
            except Exception:
                self._title = (now, None)
        return self._title[1]

    def suggest(self) -> dict | None:
        found = self._match(self._title_now())
        return None if found is None else {k: found[k] for k in ("id", "issuer", "label")} | {"reason": "window"}

    def _match(self, title: str | None) -> dict | None:
        if not title:
            return None
        low, best = title.lower(), None
        for account in self.vault.accounts():
            issuer = account["issuer"].strip().lower()
            by_issuer = bool(issuer) and issuer in low
            by_word = any(w.lower() in low for w in account["match"])
            if by_issuer or by_word:
                score = len(issuer) if by_issuer else 0
                if best is None or score > best[0]:
                    best = (score, {**account, "by_word": by_word})
        return best[1] if best else None

    def autosuggest_tick(self) -> str | None:
        """One background check. Returns the approval id it raised, if any."""
        if not self.vault.prefs().get("autosuggest", True):
            return None
        title = self._title_now()
        found = self._match(title)
        if not found:
            return None
        if not (found["by_word"] or any(w in title.lower() for w in TWOFA_WORDS)):
            return None
        now = self.clock()
        if now - self._shown.get(title, -1e9) < SUGGEST_COOLDOWN_S:
            return None
        self._shown[title] = now
        if len(self._shown) > 200:
            self._shown = {t: s for t, s in self._shown.items() if now - s < SUGGEST_COOLDOWN_S}
        try:
            return self._raise("auth.fill", found)["approval_id"]
        except AuthError:
            return None

    def start_autosuggest(self, interval: float = 2.0) -> None:
        def loop():
            while not self._stop.wait(interval):
                try:
                    self.autosuggest_tick()
                except Exception as error:
                    log.warning("autosuggest failed: %s", type(error).__name__)
        threading.Thread(target=loop, name="piblo-autosuggest", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # ---- Piblo Key
    @staticmethod
    def _public_key(rec: dict) -> dict:
        return {"id": rec["id"], "uid_tail": rec["uid"][-4:].upper(), "name": rec["name"],
                "enrolled_at": rec["enrolled_at"], "last_tap_at": rec.get("last_tap_at")}

    def keys(self) -> list[dict]:
        return [self._public_key(k) for k in self.vault.read()["keys"]]

    def enroll(self, code: str, name: str = "") -> dict:
        uid, k_meta, k_file = piblo_key.parse_enrol_code(code)

        def apply(doc):
            if any(k["uid"] == uid.hex() for k in doc["keys"]):
                raise AuthError("duplicate", "That Piblo Key is already enrolled.")
            if len(doc["keys"]) >= piblo_key.MAX_KEYS:
                raise AuthError("too_many_keys", f"You can enrol up to {piblo_key.MAX_KEYS} Piblo Keys.")
            rec = {"id": secrets.token_hex(4), "uid": uid.hex(), "k_meta": k_meta.hex(), "k_file": k_file.hex(),
                   "name": (name.strip()[:40] or f"Piblo Key {len(doc['keys']) + 1}"),
                   "enrolled_at": int(time.time()), "last_tap_at": None, "last_ctr": -1}
            doc["keys"].append(rec)
            return self._public_key(rec)
        return {"ok": True, "key": self.vault.update(apply)}

    def delete_key(self, key_id: str) -> None:
        def apply(doc):
            before = len(doc["keys"])
            doc["keys"] = [k for k in doc["keys"] if k["id"] != key_id]
            if len(doc["keys"]) == before:
                raise AuthError("not_found", "No such key.")
        self.vault.update(apply)

    def tap(self, url: str | None, p: str | None, c: str | None) -> dict:
        p_raw, c_raw = piblo_key.parse_tap(url, p, c)

        def apply(doc):
            matched = None
            for rec in doc["keys"]:
                try:
                    uid, ctr = piblo_key.decrypt_picc(bytes.fromhex(rec["k_meta"]), p_raw)
                except ValueError:
                    continue
                if uid.hex() == rec["uid"]:
                    matched = (rec, ctr)
                    break
            if matched is None:
                raise AuthError("unknown_key", "This Piblo Key is not enrolled.")
            rec, ctr = matched
            try:
                piblo_key.verify_sun(p_raw, c_raw, bytes.fromhex(rec["k_meta"]), bytes.fromhex(rec["k_file"]))
            except ValueError:
                raise AuthError("bad_mac", "This tap could not be verified.") from None
            if ctr <= rec.get("last_ctr", -1):
                raise AuthError("replay", "This tap was already used. Tap again.")
            rec["last_ctr"], rec["last_tap_at"] = ctr, int(self.clock())
            return rec["id"]
        key_id = self.vault.update(apply)
        with self._lock:
            now = self.clock()
            self._proofs = [x for x in self._proofs if now - x[1] <= PROOF_TTL_S] + [(key_id, now)]
        return {"ok": True, "key_id": key_id, "fresh": True}

    def key_proof_ok(self, item: dict) -> bool:
        now, since = self.clock(), float(item.get("t", 0))
        with self._lock:
            return any(t >= since and now - t <= PROOF_TTL_S for _, t in self._proofs)


# ---- HTTP -----------------------------------------------------------------------------

class ImportIn(BaseModel):
    uri: str = Field(min_length=1, max_length=300_000)


class PatchIn(BaseModel):
    issuer: str | None = Field(default=None, max_length=80)
    label: str | None = Field(default=None, max_length=120)
    match: list[str] | None = Field(default=None, max_length=10)


class FillIn(BaseModel):
    submit: bool = False


class PrefsIn(BaseModel):
    autosuggest: bool | None = None
    require_key_for_show: bool | None = None


class EnrollIn(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=40)


class TapIn(BaseModel):
    url: str | None = Field(default=None, max_length=600)
    p: str | None = Field(default=None, max_length=64)
    c: str | None = Field(default=None, max_length=64)


class ConfirmIn(BaseModel):
    confirm: bool = False


def _error(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


def build_auth_router(service: AuthService) -> APIRouter:
    api = APIRouter(tags=["auth"])

    def guarded(fn):
        try:
            return fn()
        except AuthError as error:
            return _error(error.code, error.message)
        except VaultError as error:
            return _error("vault_unavailable", "Piblo could not open the codes vault.", 503)

    @api.post("/auth/codes/import")
    def import_codes(body: ImportIn):
        return guarded(lambda: service.import_uri(body.uri))

    @api.get("/auth/codes")
    def list_codes():
        return guarded(lambda: {"ok": True, "accounts": service.vault.accounts(), "locked": False})

    @api.get("/auth/codes/suggest")
    def suggest():
        return guarded(lambda: {"ok": True, "suggestion": service.suggest()})

    @api.patch("/auth/codes/{account_id}")
    def patch_code(account_id: str, body: PatchIn):
        return guarded(lambda: service.patch(account_id, body.issuer, body.label, body.match))

    @api.delete("/auth/codes/{account_id}")
    def delete_code(account_id: str, body: ConfirmIn | None = Body(default=None)):
        def run():
            if not (body and body.confirm):
                raise AuthError("not_confirmed", "Send {\"confirm\": true} to delete.")
            service.vault.delete_account(account_id)
            return {"ok": True}
        return guarded(run)

    @api.post("/auth/codes/{account_id}/fill")
    def fill_code(account_id: str, body: FillIn | None = Body(default=None)):
        return guarded(lambda: service.request(account_id, "auth.fill", bool(body and body.submit)))

    @api.post("/auth/codes/{account_id}/show")
    def show_code(account_id: str):
        return guarded(lambda: service.request(account_id, "auth.show"))

    @api.get("/auth/prefs")
    def get_prefs():
        return guarded(lambda: {"ok": True, **service.vault.prefs()})

    @api.put("/auth/prefs")
    def put_prefs(body: PrefsIn):
        return guarded(lambda: {"ok": True, **service.vault.set_prefs(
            autosuggest=body.autosuggest, require_key_for_show=body.require_key_for_show)})

    @api.post("/auth/key/enroll")
    def enroll(body: EnrollIn):
        return guarded(lambda: service.enroll(body.code, body.name))

    @api.get("/auth/key")
    def list_keys():
        return guarded(lambda: {"ok": True, "keys": service.keys()})

    @api.delete("/auth/key/{key_id}")
    def delete_key(key_id: str, body: ConfirmIn | None = Body(default=None)):
        def run():
            if not (body and body.confirm):
                raise AuthError("not_confirmed", "Send {\"confirm\": true} to delete.")
            service.delete_key(key_id)
            return {"ok": True}
        return guarded(run)

    @api.post("/auth/key/tap")
    def key_tap(body: TapIn):
        return guarded(lambda: service.tap(body.url, body.p, body.c))

    return api


def install_auth(api, buddy, control_dir: str | Path, *, autosuggest: bool | None = None, **kwargs) -> AuthService:
    """Wire Piblo Keys into the app. The vault is created lazily, on first use."""
    import os

    service = AuthService(Path(control_dir).expanduser() / "auth", buddy, **kwargs)
    api.include_router(build_auth_router(service))
    if autosuggest is None:
        autosuggest = sys.platform == "win32" and not os.environ.get("PYTEST_CURRENT_TEST") \
            and os.environ.get("PIBLO_AUTH_AUTOSUGGEST", "1") != "0"
    if autosuggest:
        service.start_autosuggest()
    return service
