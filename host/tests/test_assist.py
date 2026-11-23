import asyncio
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost import intents
from mazhost.assist import ApprovalGate, AssistPipeline, Metrics
from mazhost.assist_routes import build_assist_router
from mazhost.command_router import RouteResult
from mazhost.universal_registry import Capability, EventBus, PermissionPolicy, UniversalCapabilityRegistry

H = {"Authorization": "Bearer test-token-that-is-not-default"}


class FakeBuddy:
    """Stands in for Buddy's approval queue: decisions are set by the test."""

    def __init__(self):
        self.items, self.decisions = {}, {}

    def request(self, tool, summary, **kw):
        rid = f"ap-{len(self.items) + 1}"
        self.items[rid] = {"tool": tool, "summary": summary}
        return {"id": rid, "decision": None}

    def state(self, rid, wait=0):
        return {"id": rid, "decision": self.decisions.get(rid)}


class OfflineRouter:
    def route(self, text):
        return RouteResult("unclear", None, {}, 0.0, "local model unavailable")


def pipeline(router=None, timeout=2.0):
    buddy = FakeBuddy()
    gate = ApprovalGate(buddy)
    reg = UniversalCapabilityRegistry(EventBus(), PermissionPolicy(None, approval_ok=gate.approval_ok))
    calls = []

    def find(args, context):
        return {"found": True, "device": {"id": "phone", "actions": [{"kind": "ring", "label": "Ring it"}]},
                "reply": "Pixel: close, probably in the same room."}

    def ring(args, context):
        calls.append(("ring", args))
        return {"reply": "Ringing Pixel now."}

    def diagnose(args, context):
        return {"reply": "excel.exe has stopped responding.",
                "next": {"capability": "fix.app.end_task", "args": {"process": "excel.exe"},
                         "label": "End excel.exe",
                         "verify": {"capability": "troubleshoot.verify", "args": {"rule": "app_not_responding"}}}}

    def end_task(args, context):
        calls.append(("end", args))
        return {"reply": "Ended excel.exe."}

    reg.register(Capability("devices.find", "Find", "", {}, {}, "find-devices", risk="read", handler=find))
    reg.register(Capability("devices.ring", "Ring", "", {}, {}, "find-devices", risk="act", autonomous_ok=True,
                            handler=ring))
    reg.register(Capability("troubleshoot.diagnose", "Sort", "", {}, {}, "t", risk="read", handler=diagnose))
    reg.register(Capability("troubleshoot.verify", "Verify", "", {}, {}, "t", risk="read",
                            handler=lambda a, context: {"reply": "Fixed: checked again and it's clear."}))
    reg.register(Capability("fix.app.end_task", "End a frozen app", "", {}, {}, "t", risk="sensitive",
                            required_permissions=["pc_fix"], handler=end_task))
    reg.register(Capability("pc.mute", "Mute", "", {}, {}, "pc", risk="act", handler=lambda a, context: {"reply": "Muted."}))
    p = AssistPipeline(reg, router=router, gate=gate, metrics=Metrics(Path(tempfile.mkdtemp()) / "m.jsonl"),
                       approval_timeout_s=timeout)
    return p, buddy, calls


def test_intents():
    assert intents.match("Piblo, find my AirPods")["args"] == {"query": "AirPods"}
    assert intents.match("where are my keys?")["capability"] == "devices.find"
    assert intents.match("where did I leave off")["capability"] == "resume.packet"
    assert intents.match("carry on where I left off")["capability"] == "resume.packet"
    assert intents.match("Piblo, sort this out")["capability"] == "troubleshoot.diagnose"
    assert intents.match("why isn't this working?")["capability"] == "troubleshoot.diagnose"
    assert intents.match("ring my phone")["args"] == {"query": "phone"}
    assert intents.match("yes do it") is None or intents.match("do it")["followup"] == "confirm"
    assert intents.match("find out the weather") is None
    assert intents.match("what's the capital of France") is None


def test_ack_is_first_and_find_runs_without_model():
    p, _, _ = pipeline(router=OfflineRouter())
    run = asyncio.run(p.run_to_end("Piblo, find my phone"))
    assert run["events"][0]["type"] == "assist.ack"
    assert run["events"][0]["at_ms"] < 50
    assert run["state"] == "done" and run["tier"] == "tier-0"
    assert "same room" in run["reply"]
    assert run["next"]["capability"] == "devices.ring"


def test_ring_it_followup_uses_last_device():
    p, _, calls = pipeline()

    async def go():
        await p.run_to_end("find my phone", session="s")
        return await p.run_to_end("yes", session="s")

    run = asyncio.run(go())
    assert run["state"] == "done" and calls == [("ring", {"device_id": "phone"})]


def test_sensitive_fix_waits_for_device_approval_then_verifies():
    p, buddy, calls = pipeline()

    async def go():
        await p.run_to_end("sort this out", session="s")
        started = await p.start("go ahead", session="s")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if p.view(started["id"])["state"] == "awaiting_approval":
                break
        run = p.view(started["id"])
        assert run["state"] == "awaiting_approval" and calls == []
        buddy.decisions[run["approval_id"]] = "allow"
        return await p.wait(started["id"], 3)

    run = asyncio.run(go())
    assert run["state"] == "done"
    assert calls == [("end", {"process": "excel.exe"})]
    assert "checked again" in run["reply"]


def test_denied_or_unanswered_approval_changes_nothing():
    p, buddy, calls = pipeline(timeout=0.3)

    async def go():
        await p.run_to_end("sort this out", session="s")
        return await p.run_to_end("do it", session="s")

    run = asyncio.run(go())
    assert run["state"] == "denied" and calls == []


def test_approval_is_single_use_and_bound_to_args():
    buddy = FakeBuddy()
    gate = ApprovalGate(buddy)
    rid = gate.request("fix.app.end_task", {"pid": 1}, "End it")
    buddy.decisions[rid] = "allow"
    assert gate.approval_ok(rid, "fix.app.end_task", {"pid": 2}) is False
    assert gate.approval_ok(rid, "fix.app.end_task", {"pid": 1}) is True
    assert gate.approval_ok(rid, "fix.app.end_task", {"pid": 1}) is False
    assert gate.approval_ok("forged", "fix.app.end_task", {"pid": 1}) is False


def test_automatic_origin_needs_approval_for_act_caps_but_user_does_not():
    p, _, _ = pipeline()
    reg = p.registry
    assert asyncio.run(reg.execute("pc.mute", {}, {"origin": "user"}))["success"]
    out = asyncio.run(reg.execute("pc.mute", {}, {"origin": "voice"}))
    assert out["blocked"] and out["needs_approval"]
    # Claiming an approval id does not help.
    out = asyncio.run(reg.execute("pc.mute", {}, {"origin": "voice", "approval_id": "ap-999"}))
    assert out["blocked"]


def test_missing_permission_blocks_with_clear_reply():
    p, _, _ = pipeline()
    p.registry.policy.set("pc_fix", False)

    async def go():
        await p.run_to_end("sort this out", session="s")
        return await p.run_to_end("yes", session="s")

    run = asyncio.run(go())
    assert run["state"] == "blocked" and "Settings > Permissions" in run["reply"]


def test_offline_model_gives_useful_reply_not_hang():
    p, _, _ = pipeline(router=OfflineRouter())
    run = asyncio.run(p.run_to_end("book me a table for two"))
    assert run["state"] == "unclear" and "offline" in run["reply"]


def test_cancel_interrupts_a_waiting_run():
    p, _, calls = pipeline(timeout=30)

    async def go():
        await p.run_to_end("sort this out", session="s")
        started = await p.start("yes", session="s")
        await asyncio.sleep(0.05)
        p.cancel(started["id"])
        await asyncio.sleep(0.05)
        return p.view(started["id"])

    run = asyncio.run(go())
    assert run["state"] == "cancelled" and calls == []


def test_metrics_summary():
    p, _, _ = pipeline(router=OfflineRouter())

    async def go():
        for text in ("find my phone", "find my phone", "something vague"):
            await p.run_to_end(text)

    asyncio.run(go())
    m = p.metrics.summary()
    assert m["runs"] == 3 and m["success_rate"] == 1.0
    assert m["outcomes"]["unclear"] == 1
    assert m["total_ms"]["p50"] is not None and m["model_free_share"] >= 0.66


def test_routes_and_permission_grant_needs_device_yes():
    p, buddy, _ = pipeline()
    api = FastAPI()
    api.include_router(build_assist_router(p, p.registry, p.registry.policy))
    c = TestClient(api)
    r = c.post("/assist", json={"text": "find my phone", "wait": 2}).json()
    assert r["run"]["state"] == "done"
    assert c.get(f"/assist/runs/{r['run']['id']}").json()["run"]["reply"]
    assert c.get("/assist/runs/nope").status_code == 404
    assert c.get("/assist/metrics").json()["runs"] >= 1
    perms = {x["id"]: x for x in c.get("/permissions").json()["permissions"]}
    assert perms["browser_history"]["granted"] is False and perms["pc_fix"]["granted"] is True
    r = c.post("/permissions", json={"permission": "browser_history", "on": True}).json()
    assert r["needs_approval"] and "browser_history" not in p.registry.policy.granted()
    assert c.post("/permissions", json={"permission": "browser_history", "on": True,
                                        "approval_id": r["approval_id"]}).status_code == 403
    buddy.decisions[r["approval_id"]] = "allow"
    ok = c.post("/permissions", json={"permission": "browser_history", "on": True, "approval_id": r["approval_id"]})
    assert ok.status_code == 200 and "browser_history" in ok.json()["granted"]
    assert "pc_fix" not in c.post("/permissions", json={"permission": "pc_fix", "on": False}).json()["granted"]


def test_policy_persists_grants_and_revocations():
    path = Path(tempfile.mkdtemp()) / "p.json"
    pol = PermissionPolicy(path)
    pol.set("browser_history", True)
    pol.set("pc_fix", False)
    again = PermissionPolicy(path)
    assert "browser_history" in again.granted() and "pc_fix" not in again.granted()
