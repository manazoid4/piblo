"""Read-only Piblo remote access staging service, separate from Core."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from html import escape
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from . import private_mode
from .validation import install_validation_exception_handler

log = logging.getLogger(__name__)

SESSION_COOKIE = "piblo_remote"
SESSION_TTL = 8 * 3600
PAIR_TTL = 300
STALE_AFTER = 30
CODE_TTL = 600
CODE_TRIES = 5
RATE_WINDOW = 900
# Requests allowed per RATE_WINDOW for each bucket.
LIMITS = {"code-email": 3, "code-ip": 10, "verify-ip": 30, "pair-ip": 20}
EMAIL = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})+")
MAIL_FROM = "Piblo <piblo@mazworks.uk>"
MAX_TASKS = 32
WORKERS = Literal["Claude", "Codex", "Gemini", "OpenCode", "Hermes", "Other", "Unassigned"]
STATUSES = Literal["waiting", "running", "done", "failed"]


class PairCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{24}$")


class Claim(PairCode):
    account: str = Field(min_length=3, max_length=254)
    host: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,47}$")


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[0-9a-f]{16}$")
    worker: WORKERS
    status: STATUSES
    progress: int | None = Field(default=None, ge=0, le=100)


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[Task] = Field(max_length=MAX_TASKS)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normal_email(value: str) -> str | None:
    email = value.strip().lower()
    return email if len(email) <= 254 and EMAIL.fullmatch(email) else None


def resend_mailer(email: str, code: str) -> None:
    """Send a sign-in code through Resend; the key stays in the relay's environment."""
    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        raise RuntimeError("RESEND_API_KEY is not set")
    text = (f"Your Piblo sign-in code is {code}\n\nIt works once, for 10 minutes. "
            "If you did not ask for it, ignore this email.")
    with private_mode.client(timeout=10, trust_env=False, follow_redirects=False) as client:
        response = client.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {key}"}, json={
            "from": os.environ.get("PIBLO_REMOTE_MAIL_FROM", MAIL_FROM), "to": [email],
            "subject": f"{code} is your Piblo sign-in code", "text": text})
    if response.status_code >= 300:
        raise RuntimeError(f"Resend returned {response.status_code}")


@contextmanager
def database(path: Path):
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        yield db


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    with database(path) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS login_codes (email TEXT PRIMARY KEY REFERENCES accounts(name) ON DELETE CASCADE,
                code_hash TEXT NOT NULL, expires INTEGER NOT NULL, tries INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS limits (bucket TEXT PRIMARY KEY, start INTEGER NOT NULL, count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (key_hash TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(name), expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS pairing (key_hash TEXT PRIMARY KEY, account TEXT UNIQUE NOT NULL REFERENCES accounts(name), expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS hosts (id TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(name),
                name TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL, seen INTEGER, tasks TEXT NOT NULL DEFAULT '[]');
        """)
        if "access_hash" in {row["name"] for row in db.execute("PRAGMA table_info(accounts)")}:
            raise ValueError("This database uses the old access-key sign-in. Start the relay with a new --db file.")


def add_account(path: Path, email: str) -> str:
    """Allow one email address to sign in (beta allowlist)."""
    name = normal_email(email)
    if name is None:
        raise ValueError("Use a valid email address.")
    with database(path) as db:
        db.execute("INSERT OR IGNORE INTO accounts VALUES (?)", (name,))
    return name


def allow(db, bucket: str, kind: str) -> bool:
    """Fixed-window request limit; True while the bucket is under its limit."""
    now = int(time.time())
    row = db.execute("SELECT start, count FROM limits WHERE bucket=?", (bucket,)).fetchone()
    if row is None or row["start"] <= now - RATE_WINDOW:
        db.execute("INSERT OR REPLACE INTO limits VALUES (?, ?, 1)", (bucket, now))
        return True
    if row["count"] >= LIMITS[kind]:
        return False
    db.execute("UPDATE limits SET count=count+1 WHERE bucket=?", (bucket,))
    return True


def view(title: str, content: str, *, refresh: bool = False, status: int = 200) -> HTMLResponse:
    reload = '<meta http-equiv="refresh" content="8">' if refresh else ""
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{reload}<title>{title} · Piblo</title>
<style>body{{margin:0;background:#fbfaf7;color:#2a1a12;font:16px/1.5 system-ui,sans-serif}}
main{{max-width:42rem;padding:1.5rem;margin:auto}}h1{{line-height:1.15}}section{{background:#fffaf0;border:1px solid #dcc6a9;border-radius:1rem;padding:1rem;margin-block:1rem}}
button,input{{font:inherit;box-sizing:border-box;min-height:44px;padding:.5rem .8rem;border:1px solid #a17f64;border-radius:.55rem}}
input{{width:100%;margin-block:.3rem .9rem;background:#fff}}button{{background:#a73c25;color:white;cursor:pointer}}code{{overflow-wrap:anywhere}}li{{margin:.5rem 0}}
:focus-visible{{outline:3px solid #2a1a12;outline-offset:3px}}</style></head><body><main>
<h1>{title}</h1>{content}</main></body></html>''', status_code=status)


def create_relay(path: Path, origin: str, *, insecure_local: bool = False,
                 mailer: Callable[[str, str], None] = resend_mailer, behind_proxy: bool = False) -> FastAPI:
    url = urlsplit(origin)
    if not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("Use an origin without a path, for example https://piblo.example")
    if url.scheme != "https" and not (insecure_local and url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("Remote access requires HTTPS.")
    init_db(path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_validation_exception_handler(app)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.method == "POST":
            length = request.headers.get("content-length")
            path_without_body = request.url.path in {"/pair", "/logout"} or re.fullmatch(r"/hosts/[^/]+/revoke", request.url.path)
            if length is None and path_without_body:
                # Reverse proxies can omit Content-Length on an empty POST.
                length = "0"
            length = length or ""
            if not length.isascii() or not length.isdigit() or len(length) > 5 or int(length) > 16000:
                return JSONResponse({"detail": "Invalid request size"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        # A browser may suppress the POST Origin under no-referrer.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        return response

    def client_ip(request: Request) -> str:
        # Tailscale Funnel and other local proxies append the caller's address last.
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip() if behind_proxy else ""
        return forwarded or (request.client.host if request.client else "unknown")

    def limited(request: Request, kind: str, key: str = "") -> bool:
        with database(path) as db:
            return not allow(db, f"{kind}:{digest(key or client_ip(request))}", kind)

    def same_origin(request: Request) -> None:
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Invalid form origin")

    def account(request: Request) -> str | None:
        key = request.cookies.get(SESSION_COOKIE, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", key):
            return None
        with database(path) as db:
            row = db.execute("SELECT account FROM sessions WHERE key_hash=? AND expires>?", (digest(key), int(time.time()))).fetchone()
        return row["account"] if row else None

    def require_account(request: Request) -> str:
        name = account(request)
        if name is None:
            raise HTTPException(401, "Sign in first")
        return name

    def host_status(row) -> dict:
        seen = row["seen"]
        return {"id": row["id"], "name": row["name"], "seen": seen,
                "online": seen is not None and time.time() - seen <= STALE_AFTER,
                "tasks": json.loads(row["tasks"])}

    def sign_in(notice: str = "", status: int = 200):
        alert = f'<p role="alert">{notice}</p>' if notice else ""
        return view("Piblo away from home", alert + '''<form method="post" action="/login">
<label for="email">Your email</label><input type="email" id="email" name="email" maxlength="254" required autocomplete="email">
<button>Email me a sign-in code</button></form>''', status=status)

    def code_form(email: str, notice: str, status: int = 200):
        return view("Check your email", f'''<p role="status">{notice}</p><form method="post" action="/login/code">
<input type="hidden" name="email" value="{escape(email)}">
<label for="code">6-digit code</label><input id="code" name="code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" required autocomplete="one-time-code">
<button>Sign in</button></form><p><a href="/">Use a different email</a></p>''', status=status)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        name = account(request)
        if name is None:
            return sign_in()
        with database(path) as db:
            hosts = [host_status(row) for row in db.execute("SELECT * FROM hosts WHERE account=? ORDER BY name", (name,))]
        cards = []
        for host in hosts:
            state = "Online" if host["online"] else "Offline · showing the last update"
            rows = []
            for item in host["tasks"]:
                progress = f" · {item['progress']}%" if item["progress"] is not None else ""
                rows.append(f"<li>Task {escape(item['id'][:8])} · {escape(item['worker'])} · {escape(item['status'].title())}{progress}</li>")
            tasks = "<ul>" + "".join(rows) + "</ul>" if rows else "<p>No Craft tasks reported yet.</p>"
            cards.append(f'''<section><h2>{escape(host['name'])}</h2><p>{state}</p>{tasks}
<form method="post" action="/hosts/{host['id']}/revoke"><button>Unpair this computer</button></form></section>''')
        return view("Piblo away from home", f"<p>Signed in as {escape(name)}</p>" + ("".join(cards) or "<p>No computer paired yet.</p>") + '''
<form method="post" action="/pair"><button>Pair a computer</button></form>
<form method="post" action="/logout"><button>Sign out</button></form>''', refresh=bool(hosts))

    @app.post("/login")
    def login(request: Request, email: str = Form(max_length=254)):
        same_origin(request)
        address = normal_email(email)
        if address is None:
            return sign_in("Enter a valid email address.", 400)
        if limited(request, "code-ip") or limited(request, "code-email", address):
            return sign_in("Too many codes asked for. Wait 15 minutes and try again.", 429)
        code = f"{secrets.randbelow(10**6):06d}"
        with database(path) as db:
            allowed = db.execute("SELECT 1 FROM accounts WHERE name=?", (address,)).fetchone()
            if allowed:
                db.execute("INSERT OR REPLACE INTO login_codes VALUES (?, ?, ?, 0)",
                           (address, digest(f"{address}:{code}"), int(time.time()) + CODE_TTL))
        if allowed:
            try:
                mailer(address, code)
            except Exception as error:  # noqa: BLE001 - never echo provider detail to the browser
                log.warning("sign-in email failed: %s", type(error).__name__)
                with database(path) as db:
                    db.execute("DELETE FROM login_codes WHERE email=?", (address,))
                return sign_in("We could not send the email just now. Try again shortly.", 503)
        # Same reply either way, so the page never reveals who is allowed.
        return code_form(address, "If this email can use Piblo away from home, a 6-digit code is on its way. "
                                  "It works once, for 10 minutes.")

    @app.post("/login/code")
    def verify(request: Request, email: str = Form(max_length=254), code: str = Form(max_length=6)):
        same_origin(request)
        address = normal_email(email) or ""
        if limited(request, "verify-ip"):
            return sign_in("Too many tries. Wait 15 minutes and try again.", 429)
        now = int(time.time())
        session = secrets.token_urlsafe(32)
        with database(path) as db:
            row = db.execute("SELECT * FROM login_codes WHERE email=?", (address,)).fetchone()
            good = (row is not None and row["expires"] > now and row["tries"] < CODE_TRIES
                    and hmac.compare_digest(row["code_hash"], digest(f"{address}:{code}")))
            if not good:
                if row is not None:
                    db.execute("UPDATE login_codes SET tries=tries+1 WHERE email=?", (address,))
                return code_form(address, "That code did not work. Check it, or ask for a new one.", 401)
            db.execute("DELETE FROM login_codes WHERE email=?", (address,))
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(session), address, now + SESSION_TTL))
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(SESSION_COOKIE, session, max_age=SESSION_TTL, httponly=True,
                            secure=url.scheme == "https", samesite="strict")
        return response

    @app.post("/logout")
    def logout(request: Request):
        same_origin(request)
        with database(path) as db:
            db.execute("DELETE FROM sessions WHERE key_hash=?", (digest(request.cookies.get(SESSION_COOKIE, "")),))
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.post("/pair", response_class=HTMLResponse)
    def pair(request: Request):
        same_origin(request)
        name = require_account(request)
        code = secrets.token_urlsafe(18)
        with database(path) as db:
            db.execute("DELETE FROM pairing WHERE account=? OR expires<=?", (name, int(time.time())))
            db.execute("INSERT INTO pairing VALUES (?, ?, ?)", (digest(code), name, int(time.time()) + PAIR_TTL))
        return view("Pair a computer", f'''<p>Enter this five-minute code in the Piblo connector on your computer.</p>
<p><code id="pairing-code">{code}</code></p><a href="/">Back to tasks</a>''')

    @app.post("/api/pair/preview")
    def preview(body: PairCode, request: Request):
        if limited(request, "pair-ip"):
            raise HTTPException(429, "Too many tries")
        with database(path) as db:
            row = db.execute("SELECT account FROM pairing WHERE key_hash=? AND expires>?", (digest(body.code), int(time.time()))).fetchone()
        if row is None:
            raise HTTPException(404, "Pairing code unavailable")
        return {"account": row["account"]}

    @app.post("/api/pair/claim", status_code=201)
    def claim(body: Claim, request: Request):
        if limited(request, "pair-ip"):
            raise HTTPException(429, "Too many tries")
        key, host_id = secrets.token_urlsafe(32), secrets.token_hex(12)
        with database(path) as db:
            deleted = db.execute("DELETE FROM pairing WHERE key_hash=? AND account=? AND expires>?",
                                 (digest(body.code), body.account, int(time.time()))).rowcount
            if deleted != 1:
                raise HTTPException(404, "Pairing code unavailable")
            db.execute("INSERT INTO hosts(id,account,name,key_hash) VALUES(?,?,?,?)", (host_id, body.account, body.host, digest(key)))
        return {"device_key": key}

    @app.post("/api/snapshot")
    def snapshot(body: Snapshot, request: Request):
        authorization = request.headers.get("authorization", "")
        match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{43})", authorization)
        if not match:
            raise HTTPException(401, "Invalid device credential")
        with database(path) as db:
            updated = db.execute("UPDATE hosts SET seen=?, tasks=? WHERE key_hash=?", (
                int(time.time()), json.dumps([task.model_dump() for task in body.tasks]), digest(match.group(1)),
            )).rowcount
        if updated != 1:
            raise HTTPException(401, "Invalid device credential")
        return {"ok": True}

    @app.get("/api/hosts/{host_id}/tasks")
    def tasks(host_id: str, request: Request):
        name = require_account(request)
        with database(path) as db:
            row = db.execute("SELECT * FROM hosts WHERE account=? AND id=?", (name, host_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Computer unavailable")
        return host_status(row)

    @app.post("/hosts/{host_id}/revoke")
    def revoke(host_id: str, request: Request):
        same_origin(request)
        name = require_account(request)
        with database(path) as db:
            changed = db.execute("DELETE FROM hosts WHERE account=? AND id=?", (name, host_id)).rowcount
        if changed != 1:
            raise HTTPException(404, "Computer unavailable")
        return RedirectResponse("/", status_code=303)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    provision = actions.add_parser("add-account")
    provision.add_argument("email")
    server = actions.add_parser("serve")
    server.add_argument("--origin", required=True)
    server.add_argument("--port", type=int, default=8790)
    server.add_argument("--insecure-local", action="store_true")
    server.add_argument("--behind-proxy", action="store_true", help="trust the last X-Forwarded-For entry (Tailscale Funnel)")
    server.add_argument("--print-codes", action="store_true", help="local testing only: print codes instead of emailing")
    args = parser.parse_args()
    if args.action == "add-account":
        init_db(args.db)
        print(f"Allowed to sign in: {add_account(args.db, args.email)}")
    else:
        import uvicorn

        if args.print_codes and not args.insecure_local:
            parser.error("--print-codes needs --insecure-local")
        if not args.print_codes and not os.environ.get("RESEND_API_KEY"):
            parser.error("Set RESEND_API_KEY so the relay can email sign-in codes.")
        mailer = (lambda email, code: print(f"Sign-in code for {email}: {code}", flush=True)) if args.print_codes else resend_mailer
        uvicorn.run(create_relay(args.db, args.origin, insecure_local=args.insecure_local,
                                 mailer=mailer, behind_proxy=args.behind_proxy),
                    host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
