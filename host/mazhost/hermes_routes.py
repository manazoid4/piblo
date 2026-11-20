"""Hermes as a selectable Piblo agent: routes, approvals, cards and pairing on Piblo's own UI.

Piblo keeps its firmware, local commands and approval rules. When the owner picks Hermes
(MAZ_AGENT=hermes or POST /hermes/agent), ordinary questions go to Hermes over the gadget link
and Piblo speaks the answer in its own voice. Hermes questions become ordinary Piblo approvals:
Allow needs the device's signed physical decision (Buddy.decide), they expire fail-closed, and a
question is decided once, on the connection it came in on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import wave
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import private_mode
from .buddy import AlertIn, Buddy
from .hermes_gadget import HermesLink, TurnResult

log = logging.getLogger("mazhost.hermes")

OFFLINE = "Hermes isn't reachable. Check the Hermes gateway is running on your PC."
TIMEOUT = "Hermes took too long, so I stopped it. Try again."
STOPPED = "Stopped."
FAILED = "Hermes couldn't finish that."


class AgentChoice(BaseModel):
    agent: Literal["piblo", "hermes"]


class HermesAsk(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class HermesAgent:
    def __init__(self, cfg, buddy: Buddy | None, data_dir: Path, link_factory=HermesLink) -> None:
        self.cfg, self.buddy = cfg, buddy
        self.choice_file = Path(data_dir) / "hermes-agent.json"
        self.agent = self._load_choice() or cfg.agent
        self.link: HermesLink | None = None
        self.error = ""
        self._rids: dict[str, str] = {}
        self._pair_code = ""
        if not cfg.hermes_enabled:
            return
        if private_mode.enabled() and not private_mode.is_local_url(cfg.hermes_url):
            self.error = "private mode: Hermes gateway is not on this PC or LAN"
            return
        try:
            self.link = link_factory(
                cfg.hermes_url, Path(data_dir) / "hermes-gadget.json", name="Piblo", board="piblo",
                token=cfg.hermes_token, speaker=cfg.hermes_voice == "hermes",
                on_prompt=self.on_prompt, on_prompt_closed=self.on_prompt_closed,
                on_pairing=self.on_pairing, on_card=self.on_card, on_unsolicited=self.on_unsolicited)
        except ValueError as error:
            self.error = str(error)

    def start(self) -> None:
        if self.link:
            self.link.start()

    def stop(self) -> None:
        if self.link:
            self.link.stop()

    # ---- selection ----------------------------------------------------------------------------
    def _load_choice(self) -> str:
        try:
            value = json.loads(self.choice_file.read_text("utf-8")).get("agent", "")
        except (OSError, ValueError, AttributeError):
            return ""
        return value if value in ("piblo", "hermes") else ""

    def set_agent(self, agent: str) -> None:
        self.agent = agent
        self.choice_file.parent.mkdir(parents=True, exist_ok=True)
        self.choice_file.write_text(json.dumps({"agent": agent}), "utf-8")

    def selected(self) -> bool:
        """Hermes answers only when chosen, connected-or-trying, and private mode is off."""
        return self.agent == "hermes" and self.link is not None and not private_mode.enabled()

    def status(self) -> dict:
        base = {"agent": self.agent, "selected": self.selected(), "stt": self.cfg.hermes_stt,
                "voice": self.cfg.hermes_voice}
        if self.link is None:
            return {**base, "enabled": False, "connected": False, "paired": False,
                    "reason": self.error or "set MAZ_HERMES_ENABLED=1 to connect Hermes"}
        return {**base, **self.link.status()}

    # ---- turns --------------------------------------------------------------------------------
    def answer(self, text: str) -> dict:
        self.link.start()  # idempotent; normally already running from Core's startup
        return self._result(self.link.ask_text(text, self.cfg.hermes_timeout_s))

    def answer_audio(self, pcm: bytes, rate: int = 16000) -> dict:
        self.link.start()
        return self._result(self.link.ask_audio(pcm, rate, self.cfg.hermes_timeout_s))

    def _result(self, r: TurnResult) -> dict:
        reply = r.reply
        if r.outcome == "success" and not reply and r.cards:
            reply = r.cards[-1].get("title") or r.cards[-1].get("body") or ""
        if r.outcome == "offline":
            reply = OFFLINE
        elif r.outcome == "unpaired":
            code = (self.link.pairing or {}).get("code", "") if self.link else ""
            spaced = " ".join(code)
            reply = (f"Hermes needs pairing. On your PC run hermes gadget pair, code {spaced}."
                     if code else "Hermes needs pairing. On your PC run hermes gadget pair.")
        elif r.outcome == "timeout":
            reply = TIMEOUT
        elif r.outcome == "cancelled":
            reply = reply or STOPPED
        elif not reply:
            reply = FAILED
        out = {"reply": reply, "provider": "hermes", "hermes": r.as_dict(), "timings": {"llm_ms": r.ms}}
        if r.outcome != "success":
            out["degraded"] = True
        if r.cards:
            out["cards"] = r.cards
        return out

    @staticmethod
    def wav_pcm(path: Path) -> bytes | None:
        """Mono 16-bit PCM from a device WAV, or None (Core's STT then handles the turn)."""
        try:
            with wave.open(str(path), "rb") as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != 16000:
                    return None
                return w.readframes(w.getnframes())
        except (wave.Error, EOFError, OSError):
            return None

    # ---- Hermes -> Piblo UI (called on the link's thread) -------------------------------------
    def _alert(self, title: str, body: str = "", priority: str = "normal", key: str = "") -> None:
        if not self.buddy:
            return
        try:
            self.buddy.post_alert(AlertIn(source="hermes", kind="info", title=(title or "Hermes")[:60],
                                          body=body[:200], priority=priority, agent="Hermes",
                                          idempotency_key=key[:80]))
        except Exception:
            log.warning("hermes: could not show alert", exc_info=True)

    def on_pairing(self, code: str, command: str) -> None:
        if code and code != self._pair_code:
            self._pair_code = code
            self._alert(f"Pair Hermes: {code}", "On your PC run: hermes gadget pair", "high", f"hermes-pair-{code}")

    def on_card(self, card: dict) -> None:
        parts = [card.get("body", "")]
        if card.get("progress") is not None:
            parts.append(f"{card['progress']}%")
        parts.extend(f"- {item}" for item in card.get("items") or [])
        self._alert(card.get("title") or "Hermes", "\n".join(p for p in parts if p))

    def on_unsolicited(self, r: TurnResult) -> None:
        if r.reply:
            self._alert("Hermes", r.reply)

    async def on_prompt(self, prompt: dict) -> bool | None:
        """A Hermes question as a Piblo approval. True only for a fresh, signed physical Allow."""
        if not self.buddy:
            return False
        if prompt.get("host_only"):
            self._alert("Hermes: approve on PC", "This command is too long to show here.", "high")
            return None
        idem = f"hermes:{prompt['device_id']}:{prompt['id']}"
        title = prompt.get("title") or "Hermes asks"
        try:
            rec = self.buddy.request(tool=f"Hermes: {title}"[:60], summary=prompt.get("text", "")[:300],
                                     agent="Hermes", idem=idem, sensitive=bool(prompt.get("sensitive")))
        except HTTPException:
            return False
        if rec.get("duplicate") and rec.get("decision") is not None:
            return False  # decided or withdrawn before: never replayed, never re-approved
        if rec.get("decision"):
            return False  # halted or locked category: Piblo's own rules deny it
        rid = rec["id"]
        self._rids[prompt["id"]] = rid
        loop = asyncio.get_running_loop()
        try:
            while True:
                state = await loop.run_in_executor(None, self.buddy.state, rid, 2.0)
                decision = state.get("decision")
                if decision == "allow" and not state.get("expired"):
                    return True
                if decision is not None or not state.get("known", True):
                    return False
        except asyncio.CancelledError:  # Hermes withdrew it, it timed out, or the link dropped
            self._withdraw(rid)
            raise
        finally:
            self._rids.pop(prompt["id"], None)

    def on_prompt_closed(self, prompt_id: str) -> None:
        self._withdraw(self._rids.pop(prompt_id, None))

    def _withdraw(self, rid: str | None) -> None:
        """Take a Hermes question off Piblo's screen: a late Allow can then never count."""
        if rid and self.buddy:
            try:
                self.buddy.decide(rid, "cancel", by="hermes")
            except (KeyError, TimeoutError, RuntimeError, ValueError, PermissionError):
                pass


def build_hermes_router(agent: HermesAgent) -> APIRouter:
    router = APIRouter()

    @router.get("/hermes/status")
    def hermes_status():
        return agent.status()

    @router.post("/hermes/agent")
    def hermes_agent(body: AgentChoice):
        agent.set_agent(body.agent)
        return agent.status()

    @router.post("/hermes/ask")
    def hermes_ask(body: HermesAsk):
        if agent.link is None:
            raise HTTPException(503, "hermes_disabled")
        return agent.answer(body.text)

    @router.post("/hermes/cancel")
    def hermes_cancel():
        if agent.link is None:
            raise HTTPException(503, "hermes_disabled")
        try:
            agent.link.cancel()
        except Exception as error:
            raise HTTPException(503, "hermes_offline") from error
        return {"ok": True}

    @router.post("/hermes/new")
    def hermes_new():
        if agent.link is None:
            raise HTTPException(503, "hermes_disabled")
        agent.link.new_session()
        return {"ok": True}

    return router
