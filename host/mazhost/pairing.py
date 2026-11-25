from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .config import Settings
from .security import Security

# Crockford-ish alphabet: no 0/O/1/I/L, so a human reading this off a small
# screen and typing it on a phone keyboard can't confuse look-alike chars.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 8
CODE_TTL_SECONDS = 300
MAX_CLAIM_ATTEMPTS_PER_CODE = 8
MAX_CLAIMS_PER_MINUTE_PER_IP = 10

# Device ("Connect to computer") pairing: docs/protocol/DEVICE-PAIRING.md
DEVICE_REQUEST_TTL_SECONDS = 120
DEVICE_APPROVED_GRACE_SECONDS = 60  # a decided record survives this long for the one poll that collects it
MAX_PENDING_DEVICE_REQUESTS = 4
MAX_DEVICE_REQUESTS_PER_MINUTE_PER_IP = 6
_DEVICE_ID_RE = re.compile(r"[A-Za-z0-9_-]{4,32}")
_DEVICE_NAME_RE = re.compile(r"[\x20-\x7e]{1,24}")


PAIR_PAGE = r"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#080b0e"><title>Pair with MAZ</title><style>
:root{color-scheme:dark}body{margin:0;background:#080b0e;color:#f2f4f5;font:15px ui-monospace,Consolas,monospace;display:grid;min-height:100vh;place-items:center}.c{box-sizing:border-box;width:min(92vw,420px);border:1px solid #27323b;background:#11171c;border-radius:12px;padding:20px}b{color:#ffcc00}p{color:#aeb9c1;line-height:1.5}input,button{box-sizing:border-box;width:100%;font:inherit;padding:13px;border-radius:8px;border:1px solid #394852;background:#080c0f;color:#f2f4f5;margin-top:10px;text-transform:uppercase;letter-spacing:.12em}button{background:#ffcc00;color:#08090a;font-weight:900}.ok{color:#62d98b}.bad{color:#ff7676}small{color:#7f909b}</style></head>
<body><form class="c" id="pair"><h1><b>MAZ</b> PAIR DEVICE</h1><p>On the Cardputer open <b>CONTROL → PAIRING + PHONE</b>, press Enter, then type the temporary 8-character code below.</p><input id="code" inputmode="text" autocomplete="one-time-code" maxlength="8" pattern="[A-Za-z2-9]{8}" placeholder="8-CHAR CODE" required autofocus><button>PAIR THIS PHONE</button><p id="status"><small>The code expires in 5 minutes and works once.</small></p></form><script>
const f=document.getElementById('pair'),s=document.getElementById('status'),c=document.getElementById('code');
f.addEventListener('submit',async e=>{e.preventDefault();s.textContent='Pairing…';try{const r=await fetch('claim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:c.value.trim().toUpperCase()})});const d=await r.json();if(!r.ok)throw new Error('Code invalid, expired, or already used.');s.className='ok';s.textContent='Paired. Opening MAZ WORK…';const login=document.createElement('form');login.method='post';login.action='/control/session/login';const token=document.createElement('input');token.type='hidden';token.name='token';token.value=d.token;login.appendChild(token);document.body.appendChild(login);login.submit()}catch(err){s.className='bad';s.textContent=err.message;c.select()}});
</script></body></html>"""


def _generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


@dataclass
class _Pending:
    expires_at: float
    attempts: int = 0
    used: bool = False


class PairingStore:
    """In-memory, single-process, single-outstanding-code pairing state.

    One hot pairing window at a time is enough for a handheld pairing flow
    (someone is standing at the host, watching the code, pairing one new
    client) and keeps this free of any persistence layer. Starting a new
    pairing invalidates whatever code was previously outstanding, so a code
    displayed once and forgotten can never be claimed later.
    """

    def __init__(self) -> None:
        # Exactly one outstanding pairing session at a time. The attempt
        # budget below is charged to *this session*, not to whichever digest
        # a guess happens to hash to — a wrong guess must burn down the
        # budget exactly like a right one, or the attempt cap does nothing
        # against brute force (a guess that doesn't match anything was
        # never touching the real entry's counter).
        self._current_digest: str | None = None
        self._current: _Pending | None = None
        self._claim_attempts_by_ip: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def start(self) -> tuple[str, int]:
        code = _generate_code()
        with self._lock:
            self._current_digest = _hash_code(code)
            self._current = _Pending(expires_at=time.monotonic() + CODE_TTL_SECONDS)
        return code, CODE_TTL_SECONDS

    def _rate_limited(self, client_ip: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._claim_attempts_by_ip.get(client_ip, []) if now - t < 60]
        recent.append(now)
        self._claim_attempts_by_ip[client_ip] = recent
        return len(recent) > MAX_CLAIMS_PER_MINUTE_PER_IP

    def claim(self, code: str, client_ip: str) -> bool:
        """True iff the code is valid, unused, unexpired, and the caller
        isn't rate-limited. Every failure path returns the same False with
        no distinguishing signal — a claimant must not be able to tell
        "wrong code" from "no code outstanding" from "already used" from
        "rate limited". That distinction is exactly what would let repeated
        guessing narrow down a live credential.
        """
        with self._lock:
            if self._rate_limited(client_ip):
                return False
            if self._current is None:
                return False
            self._current.attempts += 1
            expired = time.monotonic() > self._current.expires_at
            exhausted = self._current.used or self._current.attempts > MAX_CLAIM_ATTEMPTS_PER_CODE or expired
            matches = _hash_code(code) == self._current_digest
            if exhausted or not matches:
                if exhausted:
                    self._current = None
                    self._current_digest = None
                return False
            self._current.used = True
            self._current = None
            self._current_digest = None
            return True


@dataclass
class _DeviceRequest:
    request_id: str
    code: str
    device_id: str
    name: str
    ip: str
    created: float
    expires_at: float
    state: str = "pending"  # pending | approved | denied
    approval_proof: bool = False
    approval_key: str = ""


class DevicePairingStore:
    """In-memory device pairing requests. The token is never stored here: it is
    read from settings only inside the one poll that follows an approval."""

    def __init__(self, device_keys: dict[str, str] | None = None, save_keys=None) -> None:
        self._reqs: dict[str, _DeviceRequest] = {}
        self.device_keys = device_keys if device_keys is not None else {}
        self.save_keys = save_keys or (lambda: None)
        self._attempts_by_ip: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for rid in [r.request_id for r in self._reqs.values() if r.state != "pending" and now > r.expires_at]:
            del self._reqs[rid]

    def create(self, device_id: str, name: str, ip: str, approval_proof: bool = False) -> _DeviceRequest | None:
        """None means 429 (per-IP rate limit or too many pending)."""
        with self._lock:
            if approval_proof and self.device_keys and os.getenv('PIBLO_ASK_ALLOW_ENROLL') != '1':
                raise HTTPException(status.HTTP_409_CONFLICT, 'device_already_enrolled')
            now = time.monotonic()
            self._purge(now)
            recent = [t for t in self._attempts_by_ip.get(ip, []) if now - t < 60]
            recent.append(now)
            self._attempts_by_ip[ip] = recent
            if len(recent) > MAX_DEVICE_REQUESTS_PER_MINUTE_PER_IP:
                return None
            for rid in [r.request_id for r in self._reqs.values() if r.device_id == device_id]:
                del self._reqs[rid]  # a new request replaces that device's older one
            live = [r for r in self._reqs.values() if r.state == "pending" and now <= r.expires_at]
            if len(live) >= MAX_PENDING_DEVICE_REQUESTS:
                return None
            req = _DeviceRequest(
                request_id=secrets.token_urlsafe(16),
                code=f"{secrets.randbelow(1_000_000):06d}",
                device_id=device_id, name=name, ip=ip,
                created=now, expires_at=now + DEVICE_REQUEST_TTL_SECONDS,
                approval_proof=approval_proof,
            )
            self._reqs[req.request_id] = req
            return req

    def poll(self, request_id: str, ip: str) -> tuple[str, _DeviceRequest | None] | None:
        """pending/approved/denied/expired, or None (= 404). Terminal states are returned once, then deleted."""
        with self._lock:
            now = time.monotonic()
            req = self._reqs.get(request_id)
            if req is None or req.ip != ip:
                return None
            if req.state == "pending":
                if now <= req.expires_at:
                    return "pending", None
                del self._reqs[request_id]
                return "expired", None
            del self._reqs[request_id]
            return (req.state, req) if now <= req.expires_at else ("expired", None)

    def pending(self) -> list[dict]:
        with self._lock:
            now = time.monotonic()
            live = sorted(
                (r for r in self._reqs.values() if r.state == "pending" and now <= r.expires_at),
                key=lambda r: r.created,
            )
            return [
                {"request_id": r.request_id, "code": r.code, "name": r.name, "device_id": r.device_id,
                 "ip": r.ip, "age_s": int(now - r.created)}
                for r in live
            ]

    def decide(self, request_id: str, approve: bool, code: str | None = None) -> bool:
        with self._lock:
            now = time.monotonic()
            req = self._reqs.get(request_id)
            if req is None or req.state != "pending" or now > req.expires_at:
                return False
            if approve and req.approval_proof:
                if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code) or not secrets.compare_digest(code, req.code):
                    raise HTTPException(status.HTTP_403_FORBIDDEN, 'invalid_pairing_code')
                if self.device_keys and os.getenv('PIBLO_ASK_ALLOW_ENROLL') != '1':
                    raise HTTPException(status.HTTP_409_CONFLICT, 'device_already_enrolled')
                key = secrets.token_hex(32)
                old_key = self.device_keys.get(req.device_id)
                self.device_keys[req.device_id] = key
                try:
                    self.save_keys()
                except Exception:
                    if old_key is None:
                        self.device_keys.pop(req.device_id, None)
                    else:
                        self.device_keys[req.device_id] = old_key
                    raise
                req.approval_key = key
            req.state = "approved" if approve else "denied"
            req.expires_at = now + DEVICE_APPROVED_GRACE_SECONDS
            return True


class ClaimRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)


def build_pairing_app(
    settings: Settings, security: Security, store: PairingStore | None = None,
    device_keys: dict[str, str] | None = None, save_keys=None,
    audit=None, device_store: DevicePairingStore | None = None,
) -> FastAPI:
    pairing_store = store or PairingStore()
    pair_app = FastAPI(title="MAZ Pairing")

    @pair_app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        # Public by design: the page contains no credential and a claimant
        # still needs the short-lived, rate-limited, single-use device code.
        return HTMLResponse(PAIR_PAGE)

    @pair_app.post("/start", dependencies=[Depends(security.authorize)])
    def start() -> dict:
        # Minting a code requires the real long-lived credential already —
        # this is how an *authenticated* host/Cardputer session invites a
        # *new* client in, not a way to obtain the first credential.
        code, ttl = pairing_store.start()
        return {"code": code, "expires_in_seconds": ttl}

    @pair_app.post("/claim")
    def claim(body: ClaimRequest, request: Request) -> dict:
        client_ip = request.client.host if request.client else "unknown"
        normalized = body.code.strip().upper()
        if not pairing_store.claim(normalized, client_ip):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_or_expired_code")
        return {"token": settings.token}

    device_store = device_store or DevicePairingStore(device_keys, save_keys)

    def _ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @pair_app.post("/device/request")
    async def device_request(request: Request) -> dict:
        # Raw body so every malformed input is a 400, not FastAPI's 422.
        try:
            body = await request.json()
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        device_id, name = body.get("device_id"), body.get("name")
        if body.get('approval_proof') is True and audit:
            audit('device_enrollment', {'device_id': device_id if isinstance(device_id, str) and _DEVICE_ID_RE.fullmatch(device_id) else 'invalid', 'ip': _ip(request)})
        if not (isinstance(device_id, str) and _DEVICE_ID_RE.fullmatch(device_id)
                and isinstance(name, str) and _DEVICE_NAME_RE.fullmatch(name)):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_request")
        req = device_store.create(device_id, name, _ip(request), body.get("approval_proof") is True)
        if req is None:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_requests")
        return {"request_id": req.request_id, "code": req.code, "expires_in_seconds": DEVICE_REQUEST_TTL_SECONDS}

    @pair_app.get("/device/request/{request_id}")
    def device_poll(request_id: str, request: Request) -> dict:
        result = device_store.poll(request_id, _ip(request))
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        state, req = result
        if state == "approved":
            return {"status": "approved", "token": settings.token, "port": settings.port,
                    **({"device_id": req.device_id, "approval_key": req.approval_key} if req.approval_proof else {})}
        return {"status": state}

    @pair_app.get("/device/pending", dependencies=[Depends(security.authorize)])
    def device_pending() -> dict:
        return {"requests": device_store.pending()}

    @pair_app.post("/device/{request_id}/approve", dependencies=[Depends(security.authorize)])
    async def device_approve(request_id: str, request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            body = None
        code = body.get('code') if isinstance(body, dict) else None
        if not device_store.decide(request_id, True, code):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        return {"ok": True}

    @pair_app.post("/device/{request_id}/deny", dependencies=[Depends(security.authorize)])
    def device_deny(request_id: str) -> dict:
        if not device_store.decide(request_id, False):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        return {"ok": True}

    return pair_app
