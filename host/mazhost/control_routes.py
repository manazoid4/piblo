from __future__ import annotations

import time
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent_runner import AgentRunError, AgentRunner, Provider
from . import private_mode
from .authority import AuthorityBroker, AuthorityError, Scope
from .debug_capsule import DebugCapsules
from .executor import ElevatedExecutor, ExecutionError, Shell
from .llm import Route
from .phone_control import build_phone_app
from .skill_routes import install_skill_routes
from .skill_vault import SkillVault
from .stt import SpeechToText
from .teach_capture import TeachCapture
from .teach_routes import install_teach_routes
from .work_service import WorkService
from .work_store import WorkStore, WorkStoreError
from .workflow_routes import install_workflow_routes
from .workflows import WorkflowService


class AuthorityRequestBody(BaseModel):
    task: str = Field(min_length=1, max_length=400)
    agent: str = Field(default="unknown", min_length=1, max_length=120)
    scope: Scope
    project: str = Field(default="", max_length=300)
    duration_seconds: int = Field(default=600, ge=30, le=3600)
    commands_preview: list[str] = Field(default_factory=list, max_length=20)
    files: list[str] = Field(default_factory=list, max_length=40)
    network_targets: list[str] = Field(default_factory=list, max_length=20)
    requires_admin: bool = False


class ExecuteBody(BaseModel):
    grant_token: str = Field(min_length=20, max_length=5000)
    command: str = Field(min_length=1, max_length=20_000)
    cwd: str = Field(default="", max_length=1000)
    shell: Shell = "powershell"
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    agent: str = Field(default="", max_length=120)
    requires_admin: bool = False


class AgentRunRequest(BaseModel):
    provider: Provider
    prompt: str = Field(min_length=1, max_length=40_000)
    project: str = Field(min_length=1, max_length=160)
    grant_token: str = Field(min_length=20, max_length=5000)


class CrewExecuteRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8000)
    project: str = Field(min_length=1, max_length=160)
    grant_token: str = Field(min_length=20, max_length=5000)
    route: Route = "auto"


class DebugRequest(BaseModel):
    project: str = Field(default="", max_length=160)


class CardputerWorkIncrement(BaseModel):
    event_id: str = Field(min_length=1, max_length=80)
    track_id: str = Field(min_length=1, max_length=80)
    event_type_id: str = Field(min_length=1, max_length=120)


def install_control_routes(
    api: FastAPI,
    *,
    settings,
    broker: AuthorityBroker,
    executor: ElevatedExecutor,
    capsules: DebugCapsules,
    core_service,
    core_jobs,
    system_telemetry,
    model_router,
    nudge_client,
    device_monitor,
    work_store: WorkStore | None = None,
    voices=None,
) -> None:
    work_store = work_store or WorkStore(settings.work_dir)
    work_store.bootstrap()
    work_service = WorkService(work_store)
    api.mount("/control", build_phone_app(settings, broker, work_store=work_store, voices=voices))

    # Bearer-token boundary (same as every other Cardputer-facing route on
    # this app, e.g. /core/cardputer/status) — the firmware never holds a
    # phone-session cookie, so this cannot live under /control.
    @api.get("/work/cardputer")
    def work_cardputer():
        return work_service.cardputer_payload()

    @api.post("/work/cardputer/increment")
    def work_cardputer_increment(body: CardputerWorkIncrement):
        try:
            event, created = work_store.create_event(
                event_id=body.event_id,
                track_id=body.track_id,
                event_type_id=body.event_type_id,
                value=1,
                occurred_at=time.time(),
                source="cardputer_manual",
                note="Cardputer quick increment",
                session_id="cardputer",
            )
        except WorkStoreError as error:
            raise HTTPException(400, str(error)) from error
        return {"ok": True, "event": event, "created": created}

    workflows = WorkflowService(settings, model_router, core_service, nudge_client)
    install_workflow_routes(api, workflows)
    install_teach_routes(api, TeachCapture(settings, SpeechToText(settings)))
    install_skill_routes(api, SkillVault(settings, model_router))
    agents = AgentRunner(settings, broker)

    @api.get("/authority/token-id")
    def authority_token_id():
        return {"token_id": broker.token_id, "phone_url": "/control/"}

    @api.post("/authority/request")
    def authority_request(body: AuthorityRequestBody):
        project = body.project
        if body.scope == "project_full":
            try:
                project = str(core_service.project(body.project)["path"])
            except Exception as error:
                raise HTTPException(400, f"project_not_found:{body.project}") from error
        try:
            return broker.request(
                task=body.task,
                agent=body.agent,
                scope=body.scope,
                project=project,
                duration_seconds=body.duration_seconds,
                commands_preview=body.commands_preview,
                files=body.files,
                network_targets=body.network_targets,
                requires_admin=body.requires_admin,
            )
        except AuthorityError as error:
            raise HTTPException(400, str(error)) from error

    @api.get("/authority/request/{request_id}")
    def authority_request_status(request_id: str):
        try:
            return broker.request_status(request_id, reveal_grant=True)
        except AuthorityError as error:
            raise HTTPException(404, str(error)) from error

    @api.get("/authority/grants")
    def authority_grants():
        return {"ok": True, "grants": broker.active_grants()}

    @api.post("/authority/revoke/{grant_id}")
    def authority_revoke(grant_id: str):
        try:
            return {"ok": True, "grant": broker.revoke(grant_id)}
        except AuthorityError as error:
            raise HTTPException(404, str(error)) from error

    @api.post("/authority/revoke-all")
    def authority_revoke_all():
        return {"ok": True, "revoked": broker.revoke_all()}

    @api.post("/authority/execute")
    def authority_execute(body: ExecuteBody):
        try:
            return executor.run(
                grant_token=body.grant_token,
                command=body.command,
                cwd=body.cwd,
                shell=body.shell,
                timeout_seconds=body.timeout_seconds,
                agent=body.agent,
                requires_admin=body.requires_admin,
            )
        except ExecutionError as error:
            raise HTTPException(403, str(error)) from error

    # ------------------------------------------------------- installed agents
    @api.get("/agents/providers")
    def agent_providers():
        return {"ok": True, "providers": agents.providers()}

    @api.post("/agents/run")
    def agent_run(body: AgentRunRequest):
        private_mode.guard_subprocess("agent CLI")
        try:
            project = core_service.project(body.project)
            return {"ok": True, "job": agents.start(
                provider=body.provider,
                prompt=body.prompt,
                project=str(project["path"]),
                grant_token=body.grant_token,
            )}
        except (AgentRunError, Exception) as error:
            # Core project lookup and runner errors are both explicit; do not
            # silently fall back to running an agent in an arbitrary cwd.
            raise HTTPException(400, str(error)) from error

    @api.get("/agents/job/{job_id}")
    def agent_job(job_id: str):
        try:
            return {"ok": True, "job": agents.job(job_id)}
        except AgentRunError as error:
            raise HTTPException(404, str(error)) from error

    @api.get("/agents/jobs")
    def agent_jobs(limit: int = 20):
        return {"ok": True, "jobs": agents.jobs(limit)}

    @api.post("/agents/job/{job_id}/cancel")
    def agent_cancel(job_id: str):
        try:
            return {"ok": True, "job": agents.cancel(job_id)}
        except AgentRunError as error:
            raise HTTPException(409, str(error)) from error

    # CREW planning is available without elevation. Execution is a separate
    # endpoint and requires a signed PROJECT FULL-or-broader phone grant.
    @api.post("/work/crew/execute")
    def crew_execute(body: CrewExecuteRequest):
        private_mode.guard_subprocess("agent crew")
        try:
            project = core_service.project(body.project)
            plan = workflows.crew(body.task, body.project, body.route)
            return {"ok": True, "crew": agents.start_crew(
                plan=plan,
                task=body.task,
                project=str(project["path"]),
                grant_token=body.grant_token,
            )}
        except (AgentRunError, Exception) as error:
            raise HTTPException(400, str(error)) from error

    @api.get("/work/crew/job/{job_id}")
    def crew_job(job_id: str):
        try:
            return {"ok": True, "crew": agents.crew_job(job_id)}
        except AgentRunError as error:
            raise HTTPException(404, str(error)) from error

    @api.get("/work/crew/jobs")
    def crew_jobs(limit: int = 20):
        return {"ok": True, "jobs": agents.crew_jobs(limit)}

    # ------------------------------------------------------------- debugging
    @api.post("/debug/capsule")
    def debug_capsule(body: DebugRequest):
        project = body.project
        return capsules.collect(
            project=project,
            core_status=core_service.status,
            project_status=core_service.project,
            jobs_status=lambda: {"jobs": core_jobs.recent(20)},
            system_status=system_telemetry.snapshot,
            model_status=model_router.status,
            nudge_status=nudge_client.status,
            device_status=device_monitor.status,
            authority_status=lambda: {
                "token_id": broker.token_id,
                "pending": len(broker.pending()),
                "active_grants": broker.active_grants(),
            },
        )

    @api.get("/debug/capsules")
    def debug_capsules():
        return {"ok": True, "capsules": capsules.recent()}

    @api.get("/debug/capsule/{capsule_id}")
    def debug_capsule_get(capsule_id: str):
        try:
            return capsules.get(capsule_id)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(404, str(error)) from error
