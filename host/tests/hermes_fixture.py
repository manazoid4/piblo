"""A real Hermes gadget gateway for tests: the SDK's production DeviceHub with a scripted delegate.

The hub (hermes-gadget-sdk ``plugin/hub.py``, MIT) owns the handshake, enrollment, HMAC auth,
pairing frames, heartbeats, audio assembly, cards and prompts exactly as Hermes runs it; only the
"brain" (what Hermes would answer) is scripted. Install: pip install hermes-gadget (see CI).
"""

from __future__ import annotations

import asyncio
import threading
import uuid

import pytest

hub_mod = pytest.importorskip("hermes_gadget_plugin.hub", reason="hermes-gadget SDK not installed")
store_mod = pytest.importorskip("hermes_gadget_plugin.store")


class LoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro, timeout: float = 10):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


class ScriptedHermes(hub_mod.HubDelegate):
    """Pairs like Hermes (code until approved) and answers like an agent turn."""

    def __init__(self, require_pairing: bool = True) -> None:
        self.require_pairing = require_pairing
        self.approved: set[str] = set()
        self.codes: dict[str, str] = {}
        self.hub = None
        self.texts: list[str] = []
        self.utterances: list[tuple[float, int]] = []
        self.answers: list[tuple[str, bool]] = []
        self.prompt_ttl = 5
        self.reply_audio = b""

    async def is_paired(self, session) -> bool:
        return not self.require_pairing or session.device_id in self.approved

    async def on_ready(self, session) -> None:
        if session.paired:
            return
        code = next((c for c, d in self.codes.items() if d == session.device_id), None) or "ABCD2345"
        self.codes[code] = session.device_id
        await session.send_pairing(code, f"hermes pairing approve gadget {code}")

    async def approve(self, code: str) -> bool:
        device_id = self.codes.pop(code, None)
        if not device_id:
            return False
        self.approved.add(device_id)
        session = self.hub.get(device_id)
        if session:
            await session.set_paired(True)
        return True

    async def _turn(self, session, reply: str, heard: str | None = None) -> None:
        turn = uuid.uuid4().hex[:8]
        await session.turn_start(turn)
        if heard is not None:
            await session.send_transcript(heard)
        await session.send_status("Thinking")
        await session.send_delta(reply[: max(1, len(reply) // 2)], turn=turn)
        await session.send_reply(reply, turn=turn)
        if self.reply_audio and session.has_speaker:
            out = await session.play_pcm(self.reply_audio, 16000, turn=turn)
            await out.wait_played()
        await session.turn_end(turn, "success")

    async def on_text(self, session, msg_id: str, text: str) -> None:
        self.texts.append(text)
        if text == "card":
            await session.show_card("Shopping", "This week", ttl_s=0, items=["Milk", "Eggs"], progress=40)
            await self._turn(session, "Card sent")
        elif text.startswith("run "):
            await session.ask("q-" + text[4:], "Allow this command?", text[4:], ttl_s=self.prompt_ttl)
            await self._turn(session, "Asked for approval")
        elif text == "slow":
            await asyncio.sleep(30)
        else:
            await self._turn(session, f"Hermes says: {text}")

    async def on_utterance(self, session, msg_id: str, wav: bytes, seconds: float) -> None:
        self.utterances.append((round(seconds, 2), len(wav)))
        await self._turn(session, f"Heard {seconds:.1f} seconds", heard="what time is it")

    async def on_prompt_reply(self, session, prompt_id: str, yes: bool) -> None:
        self.answers.append((prompt_id, yes))


@pytest.fixture
def hermes_gateway(tmp_path):
    """Start a real gadget hub on an ephemeral port: yields (hub, brain, url, loop_thread)."""
    lt = LoopThread()
    made = []

    def make(require_pairing: bool = True, token: str | None = None):
        brain = ScriptedHermes(require_pairing)
        hub = hub_mod.DeviceHub(store_mod.DeviceStore(tmp_path / "hermes-server"), brain,
                                host="127.0.0.1", port=0, access_token=token, heartbeat_s=5)
        brain.hub = hub
        lt.run(hub.start())
        made.append(hub)
        return hub, brain, f"ws://127.0.0.1:{hub.bound_port}/gadget", lt

    yield make
    for hub in made:
        try:
            lt.run(hub.stop())
        except Exception:
            pass
    lt.stop()
