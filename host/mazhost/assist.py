"""One fast, interruptible pipeline for "Piblo, <do something useful>".

    text -> assist.ack (instant) -> route (Tier 0 intents / command router)
         -> permission + approval -> execute capability -> verify -> say

* Acknowledgement is published before any routing, so the device can show
  "On it" within milliseconds.
* Tier 0 intents and deterministic commands never touch a model; Tier 1 is the
  existing command router's small local model; if that is offline the reply
  says so instead of hanging.
* Anything the registry marks as needing approval goes through the existing
  Buddy approval queue (device-signed allow), is single-use, and is bound to
  the exact capability and arguments it was approved for.
* Every run can be cancelled; the timings and outcome of every run are logged
  so success rate and end-to-end latency are measured, not guessed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from . import intents
from .universal_registry import Event, UniversalCapabilityRegistry

APPROVAL_TIMEOUT_S = 120.0
MAX_RUNS = 200


def _args_digest(cap_id: str, args: dict) -> str:
    raw = json.dumps({"cap": cap_id, "args": args}, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:24]


class ApprovalGate:
    """Bridges registry approval checks to Buddy's device-approved queue."""

    def __init__(self, buddy) -> None:
        self.buddy = buddy
        self._issued: dict[str, str] = {}   # approval id -> digest of cap+args
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def request(self, cap_id: str, args: dict, label: str) -> str:
        item = self.buddy.request(tool=cap_id, summary=label[:120], project="piblo-assist",
                                  agent="Piblo", category="", idem="")
        with self._lock:
            self._issued[item["id"]] = _args_digest(cap_id, args)
        return item["id"]

    def decision(self, approval_id: str) -> str | None:
        return self.buddy.state(approval_id).get("decision")

    def approval_ok(self, approval_id: str, cap_id: str, args: dict) -> bool:
        with self._lock:
            if approval_id in self._used or self._issued.get(approval_id) != _args_digest(cap_id, args):
                return False
            if self.decision(approval_id) != "allow":
                return False
            self._used.add(approval_id)   # single use
            return True


class Metrics:
    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._rows: list[dict] = []
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            try:
                for line in self.path.read_text(encoding="utf-8").splitlines()[-2000:]:
                    self._rows.append(json.loads(line))
            except (OSError, ValueError):
                pass

    def record(self, row: dict) -> None:
        with self._lock:
            self._rows.append(row)
            del self._rows[:-2000]
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row) + "\n")
                except OSError:
                    pass

    def summary(self, last: int = 500) -> dict:
        with self._lock:
            rows = self._rows[-last:]
        if not rows:
            return {"runs": 0}

        def pct(values: list[float], q: float) -> float | None:
            if not values:
                return None
            values = sorted(values)
            return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 1)

        out: dict[str, Any] = {"runs": len(rows)}
        outcomes: dict[str, int] = {}
        for r in rows:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        out["outcomes"] = outcomes
        finished = [r for r in rows if r["outcome"] in ("done", "failed")]
        out["success_rate"] = round(sum(r["outcome"] == "done" for r in finished) / len(finished), 3) if finished else None
        for key in ("ack_ms", "route_ms", "exec_ms", "total_ms"):
            vals = [r[key] for r in rows if r.get(key) is not None and r["outcome"] != "awaiting_approval"]
            out[key] = {"p50": pct(vals, 0.5), "p95": pct(vals, 0.95)}
        by_cap: dict[str, dict] = {}
        for r in rows:
            cap = r.get("capability") or "(none)"
            b = by_cap.setdefault(cap, {"runs": 0, "done": 0, "total_ms": []})
            b["runs"] += 1
            b["done"] += r["outcome"] == "done"
            if r.get("total_ms") is not None:
                b["total_ms"].append(r["total_ms"])
        out["by_capability"] = {k: {"runs": v["runs"], "done": v["done"],
                                    "p50_total_ms": pct(v["total_ms"], 0.5)} for k, v in by_cap.items()}
        out["tiers"] = {}
        for r in rows:
            out["tiers"][r.get("tier", "?")] = out["tiers"].get(r.get("tier", "?"), 0) + 1
        out["model_free_share"] = round(sum(r.get("tier") in ("tier-0", "followup") for r in rows) / len(rows), 3)
        return out


class AssistPipeline:
    def __init__(self, registry: UniversalCapabilityRegistry, *, router=None, gate: ApprovalGate | None = None,
                 metrics: Metrics | None = None, approval_timeout_s: float = APPROVAL_TIMEOUT_S,
                 route_in_thread: Callable | None = None) -> None:
        self.registry = registry
        self.bus = registry.event_bus
        self.router = router
        self.gate = gate
        self.metrics = metrics or Metrics(None)
        self.approval_timeout_s = approval_timeout_s
        self.route_in_thread = route_in_thread or (lambda fn, *a: asyncio.to_thread(fn, *a))
        self.runs: "OrderedDict[str, dict]" = OrderedDict()
        self._tasks: dict[str, asyncio.Task] = {}
        self._pending_next: dict[str, dict] = {}    # session -> next step offered by the last run
        self._last_device: dict[str, str] = {}      # session -> device id ("ring it")

    # ------------------------------------------------------------------ public
    async def start(self, text: str, *, source: str = "api", session: str = "default") -> dict:
        t0 = time.perf_counter()
        run_id = "as-" + uuid.uuid4().hex[:10]
        run = {"id": run_id, "text": text[:500], "source": source, "session": session, "state": "ack",
               "events": [], "started_at": time.time(), "_t0": t0, "timings": {}}
        self.runs[run_id] = run
        while len(self.runs) > MAX_RUNS:
            old, _ = self.runs.popitem(last=False)
            self._tasks.pop(old, None)
        await self._emit(run, "assist.ack", {"text": run["text"], "say": "On it."})
        run["timings"]["ack_ms"] = self._ms(run)
        self._tasks[run_id] = asyncio.create_task(self._run(run))
        return self.view(run_id)

    async def run_to_end(self, text: str, **kw) -> dict:
        started = await self.start(text, **kw)
        task = self._tasks.get(started["id"])
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        return self.view(started["id"])

    async def wait(self, run_id: str, timeout: float) -> dict:
        task = self._tasks.get(run_id)
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return self.view(run_id)

    def cancel(self, run_id: str) -> dict:
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        return self.view(run_id)

    def cancel_session(self, session: str) -> int:
        n = 0
        for run_id, run in self.runs.items():
            task = self._tasks.get(run_id)
            if run["session"] == session and task and not task.done():
                task.cancel()
                n += 1
        self._pending_next.pop(session, None)
        return n

    def has_pending(self, session: str) -> bool:
        return session in self._pending_next

    def view(self, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return {k: v for k, v in run.items() if not k.startswith("_")}

    # ------------------------------------------------------------------ core
    async def _run(self, run: dict) -> None:
        outcome = "failed"
        try:
            outcome = await self._run_inner(run)
        except asyncio.CancelledError:
            outcome = "cancelled"
            run["state"] = "cancelled"
            await self._emit(run, "assist.cancelled", {"say": "Stopped."})
            raise
        except Exception as error:  # never leave a run hanging
            run["state"] = "failed"
            run["error"] = f"{type(error).__name__}: {error}"[:200]
            await self._emit(run, "assist.failed", {"error": run["error"],
                                                    "say": "Something went wrong; nothing was changed."})
        finally:
            run["timings"]["total_ms"] = self._ms(run)
            self.metrics.record({"at": run["started_at"], "source": run["source"],
                                 "capability": run.get("capability"), "tier": run.get("tier"),
                                 "outcome": outcome, **run["timings"]})

    async def _run_inner(self, run: dict) -> str:
        session = run["session"]
        route = await self._route(run)
        run["timings"]["route_ms"] = self._ms(run)
        cap_id, args = route["capability"], dict(route.get("args") or {})

        if route.get("followup") == "cancel":
            self._pending_next.pop(session, None)
            return await self._finish(run, "done", "Okay, leaving it.")
        if route.get("followup") == "confirm":
            pending = self._pending_next.pop(session, None)
            if pending:
                cap_id, args = pending["capability"], dict(pending.get("args") or {})
                run["verify"] = pending.get("verify")
            elif "carry on" in run["text"].lower():
                cap_id, args = "resume.packet", {}
            elif intents.SORT_OUT.match(" ".join(run["text"].split())):
                cap_id, args = "troubleshoot.diagnose", {}
            else:
                return await self._finish(run, "done", "There's nothing waiting for a yes.")
        if cap_id == "devices.ring" and not args.get("device_id"):
            q = str(args.get("query", "")).lower()
            if q in ("it", "them", "that") and session in self._last_device:
                args = {"device_id": self._last_device[session]}

        if not cap_id:
            return await self._finish(run, "unclear", route.get("say") or
                                      "I'm not sure what you'd like. Try 'find my AirPods', "
                                      "'sort this out' or 'carry on where I left off'.")
        cap = self.registry.get(cap_id)
        if cap is None:
            return await self._finish(run, "unclear", "I can't do that yet.")
        run["capability"] = cap_id
        await self._emit(run, "assist.routed", {"capability": cap_id, "args": args, "tier": run.get("tier"),
                                                "confidence": route.get("confidence", 1.0)})

        context = {"origin": "assist", "source": run["source"], "session": session}
        result = await self.registry.execute(cap_id, args, context)
        if result.get("blocked") and result.get("needs_approval"):
            if not self.gate:
                return await self._finish(run, "blocked", "That needs your approval, and no approval device is set up.")
            approval_id = self.gate.request(cap_id, args, f"Piblo wants to: {cap.name}")
            run["state"] = "awaiting_approval"
            run["approval_id"] = approval_id
            await self._emit(run, "assist.awaiting_approval",
                             {"approval_id": approval_id, "capability": cap_id,
                              "say": f"Check your Piblo to approve: {cap.name}."})
            decision = await self._await_decision(approval_id)
            if decision != "allow":
                return await self._finish(run, "denied", "Okay, I won't." if decision == "deny"
                                          else "No answer, so I left it alone.")
            run["state"] = "executing"
            result = await self.registry.execute(cap_id, args, {**context, "approval_id": approval_id})
        elif result.get("blocked"):
            missing = ", ".join(result.get("missing", []))
            return await self._finish(run, "blocked", f"Piblo doesn't have permission for that yet ({missing}). "
                                      "Turn it on in Settings > Permissions.")
        run["timings"]["exec_ms"] = self._ms(run)
        if not result.get("success"):
            return await self._finish(run, "failed", _friendly_error(result.get("error", "")))

        value = result.get("result") or {}
        run["result"] = value
        if cap_id == "devices.find" and isinstance(value, dict) and value.get("found"):
            self._last_device[session] = value["device"]["id"]
            actions = value["device"].get("actions", [])
            ring = next((a for a in actions if a["kind"] == "ring"), None)
            hand = next((a for a in actions if a["kind"] == "handoff"), None)
            if ring:
                value["next"] = {"capability": "devices.ring", "args": {"device_id": value["device"]["id"]},
                                 "label": ring["label"]}
            elif hand:
                value["next"] = {"capability": "devices.handoff", "args": {"provider": hand["provider"]},
                                 "label": hand["label"]}
        if isinstance(value, dict) and value.get("next"):
            self._pending_next[session] = value["next"]
            run["next"] = value["next"]
        verify_spec = run.get("verify")
        if verify_spec:
            check = await self.registry.execute(verify_spec["capability"], verify_spec.get("args", {}),
                                                {"origin": "assist"})
            run["verification"] = check.get("result") if check.get("success") else {"error": check.get("error")}
            vreply = (run["verification"] or {}).get("reply")
            if vreply:
                value = {**value, "reply": f"{value.get('reply', '')} {vreply}".strip()}
        reply = (value.get("reply") if isinstance(value, dict) else None) or "Done."
        return await self._finish(run, "done", reply)

    async def _route(self, run: dict) -> dict:
        hit = intents.match(run["text"])
        if hit:
            run["tier"] = "followup" if hit.get("followup") else "tier-0"
            return hit
        if self.router is None:
            run["tier"] = "unclear"
            return {"capability": None}
        await self._emit(run, "assist.thinking", {"say": "Thinking..."})
        result = await self.route_in_thread(self.router.route, run["text"])
        run["tier"] = result.tier
        if result.tier == "unclear":
            say = None
            if "unavailable" in (result.reasoning or ""):
                say = ("My local model is offline, so I can only do quick commands right now: "
                       "find my device, sort this out, carry on, media and reminders.")
            return {"capability": None, "say": say}
        if result.tier == "tier-1" and result.confidence < 0.5:
            return {"capability": None, "say": "I'm not confident I understood. Could you say it another way?"}
        return {"capability": result.capability, "args": result.args, "confidence": result.confidence}

    async def _await_decision(self, approval_id: str) -> str | None:
        deadline = time.monotonic() + self.approval_timeout_s
        while time.monotonic() < deadline:
            decision = await asyncio.to_thread(self.gate.decision, approval_id)
            if decision:
                return decision
            await asyncio.sleep(0.4)
        return None

    async def _finish(self, run: dict, state: str, say: str) -> str:
        run["state"] = state
        run["reply"] = say
        await self._emit(run, "assist.say", {"say": say})
        await self._emit(run, "assist.done", {"state": state, "capability": run.get("capability")})
        return state

    async def _emit(self, run: dict, kind: str, payload: dict) -> None:
        payload = {"run_id": run["id"], "session": run["session"], **payload}
        run["events"].append({"type": kind, "at_ms": self._ms(run), **{k: v for k, v in payload.items()
                                                                       if k not in ("run_id", "session")}})
        if self.bus:
            await self.bus.publish(Event(type=kind, source="assist", payload=payload))

    @staticmethod
    def _ms(run: dict) -> float:
        return round((time.perf_counter() - run["_t0"]) * 1000, 1)


def _friendly_error(error: str) -> str:
    known = {
        "device_not_found": "I don't know that device yet. Add it in the Piblo app under Find devices.",
        "device_cannot_ring": "That device can't be rung from Piblo.",
        "process_not_found": "That app has already closed.",
        "find_query_required": "Which device should I look for?",
    }
    for key, text in known.items():
        if key in error:
            return text
    if error.startswith("refusing_to_end"):
        return "That's part of Windows, so I won't end it."
    return f"That didn't work ({error[:80]})." if error else "That didn't work."
