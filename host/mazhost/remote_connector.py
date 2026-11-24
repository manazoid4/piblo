"""Publish a minimized view of the local PibloCraft board to a paired relay."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import private_mode
from .remote_relay import MAX_TASKS, digest

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


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
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    method = "GET" if body is None else "POST"
    raw = bytearray()
    with private_mode.client(trust_env=False, follow_redirects=False, timeout=8) as client:
        with client.stream(method, url, json=body, headers=headers) as response:
            response.raise_for_status()
            for chunk in response.iter_raw(chunk_size=16384):
                raw.extend(chunk)
                if len(raw) > 262144:
                    raise ValueError("Response exceeded the board size limit.")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Unexpected response.")
    return result


def require_remote_permission(core: str, token: str, relay: str) -> None:
    if private_mode.is_local_url(relay):
        return
    if request_json(core + "/privacy", token=token).get("private_mode") is not False:
        raise private_mode.PrivateModeError("Core must have private mode off to use a public relay.")


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
    require_remote_permission(core, token, relay)
    request_json(relay + "/api/snapshot", body={"tasks": tasks}, token=key)
    return len(tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="Private device credential file")
    parser.add_argument("--insecure-local", action="store_true")
    actions = parser.add_subparsers(dest="action", required=True)
    pair = actions.add_parser("pair")
    pair.add_argument("relay")
    pair.add_argument("--host", default=os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "Piblo Core")
    run = actions.add_parser("run")
    for action in (pair, run):
        action.add_argument("--core", default="http://127.0.0.1:8787")
    run.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        core = origin(args.core, local=True)
        token = os.environ.get("PIBLO_CORE_TOKEN") or os.environ.get("MAZ_TOKEN")
        if not token:
            raise ValueError("Set PIBLO_CORE_TOKEN to this Core's local credential.")
        if args.action == "pair":
            relay = origin(args.relay, insecure_local=args.insecure_local)
            if args.state.exists():
                raise ValueError("State file already exists. Choose a new file to re-pair.")
            code = getpass.getpass("Browser pairing code: ").strip()
            require_remote_permission(core, token, relay)
            owner = request_json(relay + "/api/pair/preview", body={"code": code})["account"]
            if input(f"Pair {args.host!r} with {owner!r} at {relay}? [y/N] ").strip().lower() != "y":
                return
            args.state.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            require_remote_permission(core, token, relay)
            response = request_json(relay + "/api/pair/claim", body={"code": code, "account": owner, "host": args.host})
            fd = os.open(args.state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"relay": relay, "key": response["device_key"]}, handle)
            print("Computer paired.")
            return
        saved = json.loads(args.state.read_text(encoding="utf-8"))
        relay = origin(saved["relay"], insecure_local=args.insecure_local)
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
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 401:
                    raise ValueError("Core or relay rejected its credential. Check the local token and pairing.") from None
                if args.once:
                    raise
                delay = min(delay * 2, 30)
            except (httpx.RequestError, OSError, ValueError, KeyError):
                if args.once:
                    raise
                delay = min(delay * 2, 30)
            time.sleep(delay)
    except (ValueError, OSError, KeyError, httpx.RequestError, httpx.HTTPStatusError, private_mode.PrivateModeError) as error:
        message = str(error) if isinstance(error, (ValueError, private_mode.PrivateModeError)) else "Could not read configuration or complete the connection."
        raise SystemExit(message) from None


if __name__ == "__main__":
    main()
