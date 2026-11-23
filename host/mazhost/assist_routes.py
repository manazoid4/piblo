"""Core routes for the assist pipeline and the permission switches.

  POST /assist                       {"text", "session"?, "wait"?: seconds}  -> run (ack at once)
  GET  /assist/runs/{id}?wait=5      long-poll a run (events, reply, state)
  POST /assist/runs/{id}/cancel      interrupt one run
  POST /assist/cancel                {"session"} interrupt everything in a session
  GET  /assist/metrics               success rate, p50/p95 per stage, by capability
  GET  /permissions                  granted permissions + what each capability needs
  POST /permissions                  {"permission", "on"}  (user's own token only)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .assist import AssistPipeline
from .universal_registry import DEFAULT_GRANTED, PermissionPolicy, UniversalCapabilityRegistry

PERMISSION_HELP = {
    "notifications": "Send alerts and reminders to your Piblo devices.",
    "device_control": "Connect, pair and monitor Piblo handhelds.",
    "find_devices": "Track your own devices' Bluetooth signal and last-seen time.",
    "screen_read": "Look at your screen (screenshot + text) when you ask Piblo to sort something out.",
    "pc_fix": "Offer fixes on this PC. Every fix still needs your yes on the Piblo device.",
    "browser_history": "Read your last few browser pages so 'carry on' can reopen them.",
}


class AssistBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    session: str = Field(default="default", max_length=60)
    source: str = Field(default="api", max_length=30)
    wait: float = Field(default=0, ge=0, le=30)


class CancelBody(BaseModel):
    session: str = Field(default="default", max_length=60)


class PermissionBody(BaseModel):
    permission: str = Field(min_length=1, max_length=60)
    on: bool
    approval_id: str = Field(default="", max_length=80)


def build_assist_router(pipeline: AssistPipeline, registry: UniversalCapabilityRegistry,
                        policy: PermissionPolicy) -> APIRouter:
    router = APIRouter(tags=["assist"])

    @router.post("/assist")
    async def assist(body: AssistBody):
        run = await pipeline.start(body.text, source=body.source, session=body.session)
        if body.wait:
            run = await pipeline.wait(run["id"], body.wait)
        return {"ok": True, "run": run}

    @router.get("/assist/runs/{run_id}")
    async def get_run(run_id: str, wait: float = 0):
        try:
            return {"ok": True, "run": await pipeline.wait(run_id, min(max(wait, 0), 30))}
        except KeyError:
            raise HTTPException(404, "run_not_found")

    @router.post("/assist/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        try:
            return {"ok": True, "run": pipeline.cancel(run_id)}
        except KeyError:
            raise HTTPException(404, "run_not_found")

    @router.post("/assist/cancel")
    def cancel_session(body: CancelBody):
        return {"ok": True, "cancelled": pipeline.cancel_session(body.session)}

    @router.get("/assist/metrics")
    def metrics(last: int = 500):
        return {"ok": True, **pipeline.metrics.summary(min(max(last, 1), 2000))}

    @router.get("/permissions")
    def permissions():
        granted = set(policy.granted())
        needed = sorted({p for c in registry.capabilities.values() for p in c.required_permissions}
                        | set(PERMISSION_HELP))
        return {"ok": True, "permissions": [
            {"id": p, "granted": p in granted, "help": PERMISSION_HELP.get(p, ""),
             "used_by": sorted(c.id for c in registry.capabilities.values() if p in c.required_permissions)}
            for p in needed]}

    @router.post("/permissions")
    def set_permission(body: PermissionBody):
        # Turning a permission off is always instant. Turning on anything beyond
        # the defaults needs a device-signed yes, so a process that only holds
        # the Core token (an agent, a script) cannot grant itself access.
        needs_yes = body.on and body.permission not in DEFAULT_GRANTED and body.permission not in policy.granted()
        if needs_yes:
            gate = pipeline.gate
            if gate is None:
                raise HTTPException(409, "approval_device_required")
            args = {"permission": body.permission}
            if not body.approval_id:
                approval_id = gate.request("permission.grant", args, f"Allow Piblo: {body.permission}")
                return {"ok": False, "needs_approval": True, "approval_id": approval_id}
            if not gate.approval_ok(body.approval_id, "permission.grant", args):
                raise HTTPException(403, "approval_not_granted")
        try:
            return {"ok": True, "granted": policy.set(body.permission, body.on)}
        except ValueError as error:
            raise HTTPException(422, str(error))

    return router
