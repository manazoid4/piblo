"""Publish a minimized view of the local PibloCraft board to a paired relay."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .remote_relay import MAX_TASKS, digest

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def origin(value: str, *, local: bool = False, insecure_local: bool = False) -> str:
    value = value.rstrip("/")
    url = urlsplit(value)
    if (
        not url.hostname or url.username or url.password or url.path or url.query or url.fragment
        or (local and url.hostname not in LOOPBACK)
        or (url.scheme != "https" and not (url.scheme == "http" and url.hostname in LOOPBACK and (local or insecure_local)))
    ):
        raise ValueError("Use HTTPS for the relay and a loopback address for Core.")
    return value


def request_json(url: str, *, body: dict | None = None, token: str = "") -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=8) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise ValueError("Response exceeded the board size limit.")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Unexpected response.")
    return result


def filtered_tasks(board: dict) -> list[dict]:
    result = []
    for row in board["tasks"]:
        task_id, status = row.get("id", ""), row.get("status")
        if not isinstance(task_id, str) or not task_id.startswith("mc-") or status not in {"waiting", "running", "done", "failed"}:
            continue
        label = str(row.get("agent") or "").lower()
        worker = "Other" if label else "Unassigned"
        for name in ("Claude", "Codex", "Gemini", "OpenCode", "Hermes"):
            if re.match(rf"^{name.lower()}(?:[0-9_. -]|$)", label):
                worker = name
                break
        progress = row.get("progress")
        result.append({"id": digest(task_id)[:16], "worker": worker, "status": status,
                       "progress": progress if type(progress) is int and 0 <= progress <= 100 else None})
        if len(result) == MAX_TASKS:
            break
    return result


def publish(relay: str, core: str, token: str, key: str) -> int:
    tasks = filtered_tasks(request_json(core + "/buddy/tasks", token=token))
    request_json(relay + "/api/snapshot", body={"tasks": tasks}, token=key)
    return len(tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="Private device credential file")
    parser.add_argument("--insecure-local", action="store_true")
    actions = parser.add_subparsers(dest="action", required=True)
    pair = actions.add_parser("pair")
    pair.add_argument("relay")
    pair.add_argument("--host", default=socket.gethostname())
    run = actions.add_parser("run")
    run.add_argument("--core", default="http://127.0.0.1:8787")
    run.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "pair":
            relay = origin(args.relay, insecure_local=args.insecure_local)
            if args.state.exists():
                raise ValueError("State file already exists. Choose a new file to re-pair.")
            code = getpass.getpass("Browser pairing code: ").strip()
            owner = request_json(relay + "/api/pair/preview", body={"code": code})["account"]
            if input(f"Pair {args.host!r} with {owner!r} at {relay}? [y/N] ").strip().lower() != "y":
                return
            args.state.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            response = request_json(relay + "/api/pair/claim", body={"code": code, "account": owner, "host": args.host})
            fd = os.open(args.state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"relay": relay, "key": response["device_key"]}, handle)
            print("Computer paired.")
            return
        saved = json.loads(args.state.read_text(encoding="utf-8"))
        relay = origin(saved["relay"], insecure_local=args.insecure_local)
        core = origin(args.core, local=True)
        token = os.environ.get("PIBLO_CORE_TOKEN") or os.environ.get("MAZ_TOKEN")
        if not token:
            raise ValueError("Set PIBLO_CORE_TOKEN to this Core's local credential.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", saved["key"]):
            raise ValueError("Device credential is invalid. Pair this computer again.")
        delay = 5
        while True:
            try:
                count = publish(relay, core, token, saved["key"])
                if args.once:
                    print(f"Published {count} Craft tasks.")
                    return
                delay = 5
            except HTTPError as error:
                if error.code == 401:
                    raise ValueError("Core or relay rejected its credential. Check the local token and pairing.") from None
                if args.once:
                    raise
                delay = min(delay * 2, 30)
            except (URLError, OSError, ValueError, KeyError):
                if args.once:
                    raise
                delay = min(delay * 2, 30)
            time.sleep(delay)
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit(str(error) if isinstance(error, ValueError) else "Could not read configuration or complete the connection.") from None


if __name__ == "__main__":
    main()
