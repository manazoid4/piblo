"""Piblo Core's Hermes link against the SDK's real gadget hub (protocol-faithful, no fake JSON)."""

from __future__ import annotations

import asyncio
import json
import math
import struct
import time

import pytest

from hermes_fixture import hermes_gateway  # noqa: F401  (fixture)
from mazhost.hermes_gadget import HermesLink, auth_mac, device_id_for_key


def tone(seconds: float, rate: int = 16000) -> bytes:
    return b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 330 * i / rate)))
                    for i in range(int(rate * seconds)))


@pytest.fixture
def links():
    made: list[HermesLink] = []
    yield made
    for link in made:
        link.stop()


def paired_link(make, links, tmp_path, **kw):
    hub, brain, url, lt = make()
    link = HermesLink(url, tmp_path / "hermes-gadget.json", **kw).start()
    links.append(link)
    assert link.wait_until(lambda l: l.pairing is not None, 10), link.status()
    assert lt.run(brain.approve(link.pairing["code"]))
    assert link.wait_until(lambda l: l.paired, 5)
    return hub, brain, link, lt


def test_sdk_vectors_match_device_identity_and_hmac():
    key = bytes(range(32))
    from hermes_gadget_plugin import protocol  # the SDK's own helpers
    assert device_id_for_key(key) == protocol.device_id_for_key(key)
    assert auth_mac(key, "hg-0011223344556677", "bm9uY2U=") == protocol.auth_mac(key, "hg-0011223344556677", "bm9uY2U=")


def test_first_connection_pairs_then_reconnects_with_hmac(hermes_gateway, links, tmp_path):
    hub, brain, link, lt = paired_link(hermes_gateway, links, tmp_path)
    assert link.pairing is None and link.status()["connected"]
    assert hub.store.key_for(link.device_id) == link.key  # enrolled: the hub stored our key once
    first = link.connects
    # the gateway drops us; Core reconnects on its own and proves the key by HMAC (enrolled: true)
    lt.run(hub.get(link.device_id).close("test"))
    assert link.wait_until(lambda l: l.connects > first and l.connected, 10)
    assert link.paired  # same identity, still approved; no second pairing code
    assert link.ask_text("hello again", timeout=10).reply == "Hermes says: hello again"


def test_key_is_kept_and_never_in_status(hermes_gateway, links, tmp_path):
    hub, brain, link, lt = paired_link(hermes_gateway, links, tmp_path)
    saved = json.loads((tmp_path / "hermes-gadget.json").read_text())["key"]
    assert saved not in json.dumps(link.status())
    again = HermesLink("ws://127.0.0.1:1/gadget", tmp_path / "hermes-gadget.json")
    assert again.device_id == link.device_id


def test_unpaired_device_gets_a_code_and_refuses_to_ask(hermes_gateway, links, tmp_path):
    hub, brain, url, lt = hermes_gateway()
    shown = []
    link = HermesLink(url, tmp_path / "k.json", on_pairing=lambda code, cmd: shown.append((code, cmd))).start()
    links.append(link)
    assert link.wait_until(lambda l: l.pairing is not None, 10)
    assert shown == [("ABCD2345", "hermes pairing approve gadget ABCD2345")]
    assert link.ask_text("hi", timeout=2).outcome == "unpaired"
    assert brain.texts == []


def test_text_round_trip(hermes_gateway, links, tmp_path):
    hub, brain, link, lt = paired_link(hermes_gateway, links, tmp_path)
    result = link.ask_text("what's on today", timeout=10)
    assert result.outcome == "success" and result.reply == "Hermes says: what's on today"
    assert brain.texts == ["what's on today"]
    # the next question works without a restart
    assert link.ask_text("and tomorrow", timeout=10).reply == "Hermes says: and tomorrow"


def test_push_to_talk_audio_reaches_hermes_as_one_utterance(hermes_gateway, links, tmp_path):
    hub, brain, link, lt = paired_link(hermes_gateway, links, tmp_path)
    result = link.ask_audio(tone(1.2), timeout=10)
    assert result.outcome == "success" and result.reply == "Heard 1.2 seconds"
    assert result.transcript == "what time is it"
    seconds, wav_bytes = brain.utterances[0]
    assert seconds == 1.2 and wav_bytes > 1.2 * 16000 * 2  # assembled into a WAV by the hub


def test_reply_audio_is_received_when_hermes_voice_is_on(hermes_gateway, links, tmp_path):
    hub, brain, link, lt = paired_link(hermes_gateway, links, tmp_path, speaker=True)
    brain.reply_audio = tone(0.4)
    result = link.ask_text("speak", timeout=15)
    assert result.reply == "Hermes says: speak"
    assert result.audio_rate == 16000 and abs(len(result.audio) - len(brain.reply_audio)) <= 1280


def test_no_speaker_advertised_means_no_reply_audio(hermes_gateway, links, tmp_path):
    hub, brain, link, lt = paired_link(hermes_gateway, links, tmp_path)
    brain.reply_audio = tone(0.4)
    assert not hub.get(link.device_id).has_speaker
    assert link.ask_text("speak", timeout=10).audio == b""


def test_structured_card_payload(hermes_gateway, links, tmp_path):
    cards = []
    hub, brain, link, lt = paired_link(hermes_gateway, links, tmp_path, on_card=cards.append)
    result = link.ask_text("card", timeout=10)
    assert result.reply == "Card sent"
    assert cards == [{"title": "Shopping", "body": "This week", "items": ["Milk", "Eggs"], "progress": 40, "ttl_s": 0}]
    assert result.cards == cards


def _approval_link(make, links, tmp_path, decide):
    closed = []
    hub, brain, link, lt = paired_link(make, links, tmp_path, on_prompt=decide, on_prompt_closed=closed.append)
    return hub, brain, link, lt, closed


def test_explicit_allow_once_and_deny(hermes_gateway, links, tmp_path):
    seen = []

    async def decide(prompt):
        seen.append(prompt)
        return prompt["text"] == "ls build"

    hub, brain, link, lt, closed = _approval_link(hermes_gateway, links, tmp_path, decide)
    link.ask_text("run ls build", timeout=10)
    link.ask_text("run rm -rf build", timeout=10)
    assert link.wait_until(lambda l: len(brain.answers) == 2, 5)
    assert brain.answers == [("q-ls build", True), ("q-rm -rf build", False)]
    assert all(p["sensitive"] for p in seen) and seen[0]["title"] == "Allow this command?"


def test_unanswered_approval_expires_without_any_reply(hermes_gateway, links, tmp_path):
    async def never(prompt):
        await asyncio.sleep(60)

    hub, brain, link, lt, closed = _approval_link(hermes_gateway, links, tmp_path, never)
    brain.prompt_ttl = 1
    link.ask_text("run deploy", timeout=10)
    time.sleep(1.6)
    assert brain.answers == []  # fail closed: silence, Hermes times it out itself


def test_disconnect_withdraws_approval_and_no_late_answer(hermes_gateway, links, tmp_path):
    release = asyncio.Event()
    started = []

    async def slow(prompt):
        started.append(prompt["id"])
        await release.wait()
        return True

    hub, brain, link, lt, closed = _approval_link(hermes_gateway, links, tmp_path, slow)
    link.ask_text("run reboot", timeout=10)
    assert link.wait_until(lambda l: started, 5)
    lt.run(hub.get(link.device_id).close("drop"))
    assert link.wait_until(lambda l: closed == ["q-reboot"], 5)
    link._loop.call_soon_threadsafe(release.set)
    assert link.wait_until(lambda l: l.connected, 10)
    time.sleep(0.3)
    assert brain.answers == []  # the yes decided after the drop is never sent


def test_answered_prompt_is_never_answered_twice(hermes_gateway, links, tmp_path):
    calls = []

    async def yes(prompt):
        calls.append(prompt["id"])
        return True

    hub, brain, link, lt, closed = _approval_link(hermes_gateway, links, tmp_path, yes)
    link.ask_text("run make", timeout=10)
    assert link.wait_until(lambda l: len(brain.answers) == 1, 5)
    session = hub.get(link.device_id)
    lt.run(session.ask("q-make", "Allow this command?", "make", ttl_s=5))  # a replayed question
    time.sleep(0.3)
    assert calls == ["q-make"] and brain.answers == [("q-make", True)]


def test_host_only_command_never_gets_a_yes(hermes_gateway, links, tmp_path):
    async def yes(prompt):
        return True

    hub, brain, link, lt, closed = _approval_link(hermes_gateway, links, tmp_path, yes)
    lt.run(hub.get(link.device_id).ask("q9", "Can't show this command", "Approve it on the Hermes host", ttl_s=5))
    assert link.wait_until(lambda l: brain.answers, 5)
    assert brain.answers == [("q9", False)]


def test_timeout_cancels_the_turn(hermes_gateway, links, tmp_path):
    hub, brain, link, lt = paired_link(hermes_gateway, links, tmp_path)
    assert link.ask_text("slow", timeout=0.5).outcome == "timeout"
    assert link.ask_text("after", timeout=10).reply == "Hermes says: after"


def test_offline_gateway_fails_fast_and_reports(tmp_path, links):
    link = HermesLink("ws://127.0.0.1:9/gadget", tmp_path / "k.json").start()
    links.append(link)
    assert link.ask_text("hi", timeout=1).outcome == "offline"
    assert link.wait_until(lambda l: l.last_error, 5)
    assert not link.status()["connected"]


def test_access_token_is_sent_and_required(hermes_gateway, links, tmp_path):
    hub, brain, url, lt = hermes_gateway(require_pairing=False, token="s3cret")
    bad = HermesLink(url, tmp_path / "a.json", token="wrong").start()
    good = HermesLink(url, tmp_path / "b.json", token="s3cret").start()
    links.extend([bad, good])
    assert good.wait_until(lambda l: l.connected, 10)
    assert bad.wait_until(lambda l: l.last_error, 10) and not bad.connected
    assert "s3cret" not in json.dumps(good.status())


def test_rejects_non_websocket_urls(tmp_path):
    with pytest.raises(ValueError):
        HermesLink("http://127.0.0.1:8765/gadget", tmp_path / "k.json")
