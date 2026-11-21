"""Piblo Core as a Hermes Gadget: one WebSocket to the Hermes gateway's gadget plugin.

Piblo hardware <-> Piblo Core <-> Hermes gadget gateway (``/gadget``) <-> Hermes Agent.

Core speaks Hermes Gadget Protocol v1 (github.com/Adolanium/hermes-gadget-sdk, docs/protocol.md,
MIT) on the device's behalf, so Piblo firmware keeps its own identity, UI and approvals. Wire
helpers below follow the SDK's ``plugin/protocol.py`` (MIT, Hermes Gadget SDK contributors; see
third_party/hermes-gadget-sdk/LICENSE).

Trust: this is a second relationship, separate from Piblo<->Core pairing. Core holds its own
32-byte gadget key (enrolled once, trust on first use, then HMAC per connection) and the Hermes
owner approves the pairing code on the Hermes host. Nothing here logs the key, a token or audio.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import ssl
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from . import private_mode

log = logging.getLogger("mazhost.hermes")

PROTO = 1
SUBPROTOCOL = "hermes-gadget.v1"
AUTH_CONTEXT = b"hermes-gadget/v1|"
CHANNEL_AUDIO = 0x01
FRAME_MS = 40
DEFAULT_URL = "ws://127.0.0.1:8765/gadget"
HOST_ONLY_TITLE = "Can't show this command"
SENSITIVE_TITLES = ("Allow this command?", HOST_ONLY_TITLE)


# ---- wire helpers (after hermes-gadget-sdk plugin/protocol.py, MIT) -----------------------------
def device_id_for_key(key: bytes) -> str:
    return "hg-" + hashlib.sha256(key).hexdigest()[:16]


def auth_mac(key: bytes, device_id: str, nonce: str) -> str:
    msg = AUTH_CONTEXT + device_id.encode() + b"|" + nonce.encode()
    return base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode()


def binary(channel: int, stream: int, seq: int, payload: bytes) -> bytes:
    return struct.pack("<BBH", channel, stream & 0xFF, seq & 0xFFFF) + payload


def parse_binary(data: bytes) -> tuple[int, int, int, bytes] | None:
    if len(data) < 4:
        return None
    channel, stream, seq = struct.unpack_from("<BBH", data)
    return channel, stream, seq, bytes(data[4:])


def load_or_create_key(path: Path) -> bytes:
    """The gadget key: 32 random bytes, created once, owner-only file. Never logged."""
    try:
        key = base64.b64decode(json.loads(path.read_text("utf-8"))["key"], validate=True)
        if len(key) == 32:
            return key
    except (OSError, ValueError, KeyError, TypeError):
        pass
    key = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": base64.b64encode(key).decode()}), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    return key


# ---- results ------------------------------------------------------------------------------------
@dataclass
class TurnResult:
    outcome: str = "timeout"         # success | failure | cancelled | timeout | offline | unpaired
    reply: str = ""
    transcript: str = ""
    interim: list[str] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    audio: bytes = b""               # PCM16 reply audio, only when Hermes voice is on
    audio_rate: int = 0
    turn: str = ""
    ms: int = 0

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "audio"}
        d["audio_bytes"] = len(self.audio)
        return d


PromptHandler = Callable[[dict], Awaitable[bool | None]]


class HermesLink:
    """One gadget connection, run on its own thread and event loop; a sync API for Core's routes.

    ``on_prompt(prompt) -> True | False | None`` decides a Hermes question: True sends yes,
    False sends no, None sends nothing (closed, expired or host-only). It is called at most once
    per prompt id and its answer is only sent on the connection the prompt arrived on.
    """

    def __init__(self, url: str = DEFAULT_URL, key_file: Path | str = "hermes-gadget.json", *,
                 name: str = "Piblo", board: str = "piblo-core", firmware: str = "",
                 token: str = "", speaker: bool = False, heartbeat_s: float = 20.0,
                 on_prompt: PromptHandler | None = None,
                 on_prompt_closed: Callable[[str], None] | None = None,
                 on_pairing: Callable[[str, str], None] | None = None,
                 on_card: Callable[[dict], None] | None = None,
                 on_unsolicited: Callable[[TurnResult], None] | None = None,
                 ssl_context: ssl.SSLContext | None = None) -> None:
        if not url.startswith(("ws://", "wss://")):
            raise ValueError("hermes url must be ws:// or wss://")
        self.url, self.name, self.board, self.firmware = url, name, board, firmware
        self.token, self.speaker, self.heartbeat_s = token, speaker, heartbeat_s
        self.key = load_or_create_key(Path(key_file))
        self.device_id = device_id_for_key(self.key)
        self.on_prompt, self.on_prompt_closed = on_prompt, on_prompt_closed
        self.on_pairing, self.on_card, self.on_unsolicited = on_pairing, on_card, on_unsolicited
        self.ssl_context = ssl_context
        self.connected = False
        self.paired = False
        self.pairing: dict | None = None
        self.last_error = ""
        self.dashboard: dict | None = None
        self.status_text = ""
        self.preview = ""
        self.server: dict = {}
        self.connects = 0
        self._ws = None
        self._gen = 0                        # connection generation: answers never cross a reconnect
        self._seq = 0
        self._stream = 0
        self._pending: TurnResult | None = None
        self._pending_done: asyncio.Event | None = None
        self._pending_t0 = 0.0
        self._bound_turn: str | None = None
        self._unsolicited: dict[str, TurnResult] = {}
        self._audio_in: dict[int, bytearray] = {}
        self._audio_rate: dict[int, int] = {}
        self._audio_turn: dict[int, str] = {}
        self._prompts: dict[str, asyncio.Task] = {}
        self._answered: set[str] = set()
        self._ask_lock: asyncio.Lock | None = None
        self._ready = threading.Event()
        self._stop = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._main: asyncio.Task | None = None

    # ---- lifecycle ---------------------------------------------------------------------------
    def start(self) -> "HermesLink":
        if self._thread:
            return self
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_thread, name="hermes-gadget", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self

    def _run_thread(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ask_lock = asyncio.Lock()
        self._main = self._loop.create_task(self._forever())
        self._ready.set()
        try:
            self._loop.run_until_complete(self._main)
        except asyncio.CancelledError:
            pass
        finally:
            self._loop.close()

    def stop(self) -> None:
        self._stop = True
        if self._loop and self._main and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._main.cancel)
        if self._thread:
            self._thread.join(5)
        self._thread = None

    def _call(self, coro, timeout: float):
        if not self._loop or self._loop.is_closed():
            raise RuntimeError("hermes link not started")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def wait_until(self, check: Callable[["HermesLink"], bool], timeout: float = 10.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if check(self):
                return True
            time.sleep(0.02)
        return check(self)

    # ---- public sync API (Core's threadpool) ----------------------------------------------------
    def status(self) -> dict:
        safe_url = self.url.split("?", 1)[0]
        return {"enabled": True, "url": safe_url, "connected": self.connected, "paired": self.paired,
                "device_id": self.device_id, "pairing": dict(self.pairing) if self.pairing else None,
                "status": self.status_text, "preview": self.preview, "dashboard": self.dashboard, "server": self.server,
                "last_error": self.last_error, "connects": self.connects,
                "busy": self._pending is not None}

    def ask_text(self, text: str, timeout: float = 90.0) -> TurnResult:
        return self._call(self._ask({"type": "text", "text": text}, None, timeout), timeout + 5)

    def ask_audio(self, pcm: bytes, rate: int = 16000, timeout: float = 90.0) -> TurnResult:
        """Push-to-talk: one utterance of mono PCM16 (Hermes does speech-to-text)."""
        return self._call(self._ask(None, (pcm, rate), timeout), timeout + 5)

    def cancel(self) -> None:
        self._call(self._send({"type": "cancel"}), 5)

    def new_session(self) -> None:
        self._call(self._send({"type": "session.new"}), 5)

    # ---- connection loop ------------------------------------------------------------------------
    def _hello(self) -> dict:
        caps: dict[str, Any] = {
            # Piblo's LCD, as Core draws Hermes text into Piblo's own cards and approval queue.
            "display": {"width": 240, "height": 135, "color": True, "charset": "ascii",
                        "text_cols": 40, "text_rows": 8},
            "mic": {"rate": 16000, "format": "pcm16"},
            "inputs": ["talk", "cancel", "up", "down"], "talk_mode": "hold",
            "card": {"v": 1, "items": 6},
            "dashboard": {"v": 1, "rows": 6},
        }
        if self.speaker:
            caps["speaker"] = {"rate": 16000, "format": "pcm16"}
        hello = {"type": "hello", "proto": PROTO, "device_id": self.device_id, "name": self.name,
                 "board": self.board, "firmware": self.firmware or "core", "caps": caps}
        if self.token:
            hello["token"] = self.token
        return hello

    async def _forever(self) -> None:
        backoff = 1.0
        while not self._stop:
            started = time.monotonic()
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # refused, closed, auth: retry with backoff, never crash Core
                self.last_error = type(error).__name__ if not str(error) else str(error)[:160]
                log.info("hermes: connection ended: %s", self.last_error)
            finally:
                await self._drop()
            if time.monotonic() - started > 30:
                backoff = 1.0
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _session(self) -> None:
        kw: dict[str, Any] = {"subprotocols": [SUBPROTOCOL], "open_timeout": 8, "max_size": 1 << 20,
                              "ping_interval": None, "proxy": None}
        if self.url.startswith("wss://"):
            kw["ssl"] = self.ssl_context or ssl.create_default_context()
        private_mode.guard(self.url)  # private mode: only a gateway on this PC or LAN
        async with connect(self.url, **kw) as ws:
            await ws.send(json.dumps(self._hello()))
            challenge = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if challenge.get("type") == "error":
                raise ConnectionError(challenge.get("code") or "rejected")
            if challenge.get("type") != "challenge":
                raise ConnectionError("no challenge")
            if challenge.get("enrolled"):
                auth = {"type": "auth", "mac": auth_mac(self.key, self.device_id, challenge.get("nonce", ""))}
            else:  # trust on first use: the key crosses the network exactly once
                auth = {"type": "auth", "key": base64.b64encode(self.key).decode()}
            await ws.send(json.dumps(auth))
            welcome = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if welcome.get("type") != "welcome":
                raise ConnectionError(welcome.get("code") or "no welcome")
            self._ws, self._gen = ws, self._gen + 1
            self.connected, self.connects, self.last_error = True, self.connects + 1, ""
            self.paired = bool(welcome.get("paired"))
            self.heartbeat_s = float(welcome.get("heartbeat_s") or self.heartbeat_s)
            self.server = {k: welcome[k] for k in ("server", "proto", "session", "language") if k in welcome}
            if self.paired:
                self.pairing = None
            log.info("hermes: connected as %s (paired=%s)", self.device_id, self.paired)
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), self.heartbeat_s * 3)
                except asyncio.TimeoutError:
                    raise ConnectionError("heartbeat lost")
                if isinstance(raw, bytes):
                    self._on_binary(raw)
                else:
                    await self._on_json(json.loads(raw))

    async def _drop(self) -> None:
        """Connection gone: fail closed. Pending questions are withdrawn, never answered later."""
        self._ws, self.connected = None, False
        for pid, task in list(self._prompts.items()):
            task.cancel()
            if self.on_prompt_closed:
                self.on_prompt_closed(pid)
        self._prompts.clear()
        self._audio_in.clear()
        if self._pending is not None and self._pending_done and not self._pending_done.is_set():
            self._pending.outcome = "offline"
            self._pending_done.set()

    async def _send(self, obj: dict) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(json.dumps(obj))
            return True
        except ConnectionClosed:
            return False

    # ---- turns ----------------------------------------------------------------------------------
    async def _ask(self, text_msg: dict | None, audio: tuple[bytes, int] | None, timeout: float) -> TurnResult:
        if not self.connected:
            return TurnResult(outcome="offline")
        if not self.paired:
            return TurnResult(outcome="unpaired")
        async with self._ask_lock:
            result, done = TurnResult(), asyncio.Event()
            self._pending, self._pending_done, self._bound_turn = result, done, None
            self._pending_t0 = time.monotonic()
            msg_id = secrets.token_hex(4)
            try:
                if text_msg is not None:
                    ok = await self._send({**text_msg, "id": "t" + msg_id})
                else:
                    ok = await self._send_utterance("a" + msg_id, *audio)
                if not ok:
                    result.outcome = "offline"
                    return result
                try:
                    await asyncio.wait_for(done.wait(), timeout)
                except asyncio.TimeoutError:
                    result.outcome = "timeout"
                    await self._send({"type": "cancel"})  # stop Hermes too: nothing waits for it now
                return result
            finally:
                result.ms = round((time.monotonic() - self._pending_t0) * 1000)
                self._pending = self._pending_done = self._bound_turn = None

    async def _send_utterance(self, msg_id: str, pcm: bytes, rate: int) -> bool:
        self._stream = self._stream % 250 + 1
        stream, step = self._stream, rate * 2 * FRAME_MS // 1000
        if not await self._send({"type": "audio.start", "id": msg_id, "stream": stream, "rate": rate,
                                 "format": "pcm16", "mode": "hold"}):
            return False
        ws = self._ws
        try:
            for seq, off in enumerate(range(0, len(pcm), step)):
                await ws.send(binary(CHANNEL_AUDIO, stream, seq, pcm[off:off + step]))
        except (ConnectionClosed, AttributeError):
            return False
        return await self._send({"type": "audio.end", "id": msg_id, "stream": stream,
                                 "duration_ms": round(len(pcm) / 2 / rate * 1000)})

    def _target(self, turn: str | None) -> TurnResult | None:
        """The result a turn's messages belong to: the pending ask, or an unsolicited turn."""
        if self._pending is not None and (self._bound_turn in (None, turn) or not turn):
            if turn and self._bound_turn is None:
                self._bound_turn = turn
            return self._pending
        if turn:
            return self._unsolicited.setdefault(turn, TurnResult(turn=turn))
        return None

    def _finish(self, turn: str, outcome: str) -> None:
        if self._pending is not None and (not turn or self._bound_turn in (None, turn)):
            self._pending.outcome, self._pending.turn = outcome, turn or self._pending.turn
            self._pending_done.set()
            return
        done = self._unsolicited.pop(turn, None)
        if done and self.on_unsolicited and (done.reply or done.cards):
            done.outcome = outcome
            self.on_unsolicited(done)

    # ---- inbound ------------------------------------------------------------------------------
    async def _on_json(self, m: dict) -> None:
        kind = m.get("type")
        if kind == "ping":
            await self._send({"type": "pong", "ts": m.get("ts")})
        elif kind == "pairing":
            self.paired, self.pairing = False, {"code": m.get("code", ""), "command": m.get("command", "")}
            if self.on_pairing:
                self.on_pairing(self.pairing["code"], self.pairing["command"])
        elif kind == "paired":
            self.paired, self.pairing = True, None
        elif kind == "unpaired":
            self.paired = False
        elif kind == "turn.start":
            self._target(m.get("turn"))
        elif kind == "transcript":
            if (t := self._target(None)) is not None:
                t.transcript = m.get("text", "")
        elif kind == "status":
            self.status_text = m.get("text", "")
        elif kind == "reply.delta":
            if self._target(m.get("turn")) is not None:
                self.preview = m.get("text", "")  # cumulative; the final reply replaces it
        elif kind == "reply":
            if (t := self._target(m.get("turn"))) is not None:
                if m.get("interim"):
                    t.interim.append(m.get("text", ""))
                else:
                    t.reply = (t.reply + "\n" + m.get("text", "")).strip() if t.reply else m.get("text", "")
        elif kind == "turn.end":
            self.status_text = self.preview = ""
            self._finish(m.get("turn", ""), m.get("outcome", "success"))
        elif kind == "notice":
            if self._pending is not None:
                self._pending.notices.append(m.get("text", ""))
        elif kind == "display":
            card = {k: m[k] for k in ("id", "title", "body", "items", "progress", "ttl_s") if k in m}
            if self._pending is not None:
                self._pending.cards.append(card)
            if self.on_card:
                self.on_card(card)
        elif kind == "dashboard":
            self.dashboard = {k: m.get(k) for k in ("v", "sessions", "tiles", "widgets") if k in m}
        elif kind == "prompt":
            self._on_prompt(m)
        elif kind == "prompt.close":
            task = self._prompts.pop(m.get("id", ""), None)
            if task:
                task.cancel()
                if self.on_prompt_closed:
                    self.on_prompt_closed(m.get("id", ""))
        elif kind == "audio.start":
            s = int(m.get("stream", 0))
            self._audio_in[s], self._audio_rate[s], self._audio_turn[s] = bytearray(), int(m.get("rate", 16000)), m.get("turn", "")
        elif kind in ("audio.end", "audio.abort"):
            s = int(m.get("stream", 0))
            pcm = self._audio_in.pop(s, None)
            if kind == "audio.end" and pcm is not None and (t := self._target(self._audio_turn.get(s) or None)) is not None:
                t.audio, t.audio_rate = t.audio + bytes(pcm), self._audio_rate.get(s, 16000)
        elif kind == "action":  # Piblo advertises no device actions: answer exactly once, refuse
            await self._send({"type": "action.result", "id": m.get("id"), "ok": False,
                              "error": "Piblo does not take device actions"})
        elif kind == "diag":
            await self._send({"type": "diag", "id": m.get("id"), "report": {"client": "piblo-core"}, "log": ""})
        elif kind == "error":
            self.last_error = str(m.get("code") or "error")

    def _on_binary(self, data: bytes) -> None:
        frame = parse_binary(data)
        if frame and frame[0] == CHANNEL_AUDIO and frame[1] in self._audio_in:
            buf = self._audio_in[frame[1]]
            if len(buf) < 16000 * 2 * 120:  # cap at two minutes of reply audio
                buf += frame[3]

    def _on_prompt(self, m: dict) -> None:
        pid = str(m.get("id") or "")
        if not pid or pid in self._prompts or pid in self._answered or not self.on_prompt:
            return  # a question is decided once; a repeat after it was answered gets nothing
        gen = self._gen
        prompt = {"id": pid, "title": m.get("title", ""), "text": m.get("text", ""),
                  "ttl_s": m.get("ttl_s"), "sensitive": m.get("title", "") in SENSITIVE_TITLES,
                  "host_only": m.get("title", "") == HOST_ONLY_TITLE, "device_id": self.device_id}

        async def decide() -> None:
            try:
                ttl = float(prompt["ttl_s"]) if prompt["ttl_s"] else None
                answer = await (asyncio.wait_for(self.on_prompt(prompt), ttl) if ttl else self.on_prompt(prompt))
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return
            except Exception:
                log.warning("hermes: approval handler failed; denying", exc_info=True)
                answer = False
            if answer is None or gen != self._gen or self._prompts.get(pid) is not asyncio.current_task():
                return  # expired, withdrawn or reconnected: never answered late
            self._prompts.pop(pid, None)
            self._answered.add(pid)
            if prompt["host_only"] and answer:
                answer = False  # Hermes ignores a yes to the host-only notice; never send one
            await self._send({"type": "prompt.reply", "id": pid, "answer": "yes" if answer else "no"})

        self._prompts[pid] = asyncio.get_running_loop().create_task(decide())
