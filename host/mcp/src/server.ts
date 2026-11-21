import { McpServer } from "@modelcontextprotocol/server";
import * as z from "zod/v4";
import { CoreClient, CoreError, VERSION, cleanAgentName } from "./core.js";

export const MAX_WAIT_S = 55; // under the 60 s default client timeout and Core's own 60 s approval window

type Decision = "approved" | "rejected" | "expired" | "edit" | "undone";
const text = (value: unknown, isError = false) => ({
  content: [{ type: "text" as const, text: JSON.stringify(value) }],
  ...(isError ? { isError: true } : {}),
});
const failure = (error: unknown) =>
  error instanceof CoreError ? text({ ok: false, error: error.code, message: error.message }, true) : text({ ok: false, error: "internal", message: String(error) }, true);

interface StatusReply {
  connected: boolean;
  simulated: boolean;
  client: string | null;
  last_seen_ago_s: number | null;
  halted: boolean;
  device: { battery_pct?: number; charging?: boolean; wifi_rssi?: number };
}
interface RequestReply {
  id: string;
  decision: string | null;
  duplicate?: boolean;
  expired?: boolean;
  digest?: string;
  instruction?: string;
}

/** Map Core's decision vocabulary onto the three answers an agent may see. Anything unknown fails closed. */
export function toDecision(core: { decision: string | null; expired?: boolean }): Decision | null {
  if (core.decision === "allow") return "approved";
  if (core.decision === "deny") return "rejected";
  if (core.decision === "edit" || core.decision === "undone") return core.decision;
  if (core.decision === "cancel" || core.decision === "expired" || core.expired) return "expired";
  if (core.decision === null || core.decision === undefined) return null; // still waiting
  return "rejected";
}

export function createServer(core: CoreClient, defaultAgent: string, readOnly = false): McpServer {
  const server = new McpServer({ name: "piblo-mcp", version: VERSION });
  // Agent identity: PIBLO_AGENT_NAME, else the name the MCP app announced in its initialize call.
  const agent = () => defaultAgent || cleanAgentName(server.server.getClientVersion()?.name) || "mcp-agent";
  const status = async () => {
    const s = await core.get<StatusReply>("/buddy/status");
    if (typeof s?.connected !== "boolean" || typeof s?.simulated !== "boolean" || !s.device || typeof s.device !== "object") {
      throw new CoreError("invalid_status", "Core returned an invalid device status");
    }
    return s;
  };

  server.registerTool(
    "piblo_status",
    {
      title: "Piblo status",
      description: "Is a Piblo (real device or simulator) connected to Core right now, and when was it last seen?",
      inputSchema: z.object({}),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async () => {
      try {
        const s = await status();
        return text({
          ok: true,
          state: !s.connected ? "disconnected" : s.simulated ? "simulated" : "connected",
          client: s.client,
          last_seen_seconds_ago: s.last_seen_ago_s,
          halted: s.halted,
        });
      } catch (e) {
        return failure(e);
      }
    },
  );

  if (!readOnly) server.registerTool(
    "piblo_notify",
    {
      title: "Send a notification",
      description:
        "Show a short notification on the user's Piblo. It is information only: the user can acknowledge it, nothing in it is ever run.",
      inputSchema: z.object({
        title: z.string().min(1).max(60),
        body: z.string().max(200).optional(),
        kind: z.enum(["info", "task_done", "reminder", "deadline"]).default("info"),
        priority: z.enum(["low", "normal", "high", "urgent"]).default("normal"),
        idempotency_key: z.string().max(80).optional().describe("Same key twice = one notification"),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
    },
    async (a) => {
      try {
        const r = await core.post<{ id: string; duplicate?: boolean }>("/buddy/alert", {
          source: agent().slice(0, 40),
          agent: agent(),
          kind: a.kind,
          title: a.title,
          body: a.body ?? "",
          priority: a.priority,
          actions: ["ack"],
          idempotency_key: a.idempotency_key ?? "",
        });
        return text({ ok: true, id: r.id, duplicate: r.duplicate === true });
      } catch (e) {
        return failure(e);
      }
    },
  );

  if (!readOnly) server.registerTool(
    "piblo_task_update",
    {
      title: "Report task progress",
      description: "Tell Piblo where a task is. Use the same task_id for every update. 'done' or 'failed' also notifies the user once.",
      inputSchema: z.object({
        task_id: z.string().min(1).max(80),
        title: z.string().min(1).max(60),
        status: z.enum(["waiting", "running", "done", "failed"]),
        progress: z.number().int().min(0).max(100).optional(),
        detail: z.string().max(200).optional(),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (a) => {
      try {
        await core.post("/buddy/task", {
          id: a.task_id,
          title: a.title,
          status: a.status,
          progress: a.progress ?? null,
          detail: a.detail ?? "",
          agent: agent(),
        });
        return text({ ok: true, task_id: a.task_id, status: a.status });
      } catch (e) {
        return failure(e);
      }
    },
  );

  if (!readOnly) server.registerTool(
    "piblo_request_approval",
    {
      title: "Ask the user to approve an action",
      description:
        "Ask the user to approve or reject ONE action on their Piblo and wait for the answer (up to ~55 s). " +
        "Returns approved, rejected or expired. Treat expired exactly like rejected: do not proceed. " +
        "Set sensitive=true for money, sending, deleting or publishing: Piblo then asks for a deliberate hold where it supports that.",
      inputSchema: z.object({
        action: z.string().min(1).max(120).describe("Short name of the action, e.g. 'Run npm publish'"),
        summary: z.string().max(200).optional().describe("What exactly will happen"),
        sensitive: z.boolean().default(false),
        category: z.enum(["email", "calendar", "purchase", "delete", "deploy", "other"]).optional(),
        amount: z.string().max(16).optional(),
        recipient: z.string().max(40).optional(),
        undo: z.string().max(200).optional(),
        urgent: z.boolean().default(false),
        idempotency_key: z.string().max(80).optional().describe("Retrying with the same key never creates a second prompt"),
        wait_seconds: z.number().int().min(5).max(MAX_WAIT_S).default(MAX_WAIT_S),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
    },
    async (a) => {
      let id = "";
      try {
        const r = await core.post<RequestReply>("/buddy/request", {
          tool: a.action,
          summary: a.summary ?? "",
          agent: agent(),
          project: agent().slice(0, 60),
          sensitive: a.sensitive,
          category: a.category ?? "", amount: a.amount ?? "", recipient: a.recipient ?? "",
          undo: a.undo ?? "", urgent: a.urgent,
          nod: a.sensitive, // the Nod hold path (PR #63); Cores without it ignore the field, sensitive stays recorded
          idempotency_key: a.idempotency_key ?? "",
        });
        id = r.id;
        const end = Date.now() + a.wait_seconds * 1000;
        let latest = r;
        let d = toDecision(latest);
        while (d === null && Date.now() < end) {
          const wait = Math.max(1, Math.min(20, Math.floor((end - Date.now()) / 1000)));
          const s = await core.get<RequestReply>(`/buddy/state?id=${encodeURIComponent(id)}&wait=${wait}`, (wait + 10) * 1000);
          latest = s;
          d = toDecision(latest);
        }
        if (d === null) {
          // Nobody answered in time: close the request so a late tap cannot approve something nobody awaits.
          await core.post("/buddy/decide", { id, decision: "cancel" }).catch(() => undefined);
          d = "expired";
        }
        return text({ ok: true, decision: d, id, digest: latest.digest, instruction: latest.instruction });
      } catch (e) {
        // Fail closed: if we cannot learn the answer, the answer is no.
        if (id) await core.post("/buddy/decide", { id, decision: "cancel" }).catch(() => undefined);
        const code = e instanceof CoreError ? e.code : "internal";
        return text({ ok: false, decision: "rejected", error: code, message: e instanceof Error ? e.message : String(e) }, true);
      }
    },
  );

  server.registerTool(
    "piblo_device_state",
    {
      title: "Piblo battery and connection",
      description: "Battery level and connectivity of the user's Piblo, when it reports them. Nothing else about the device is available.",
      inputSchema: z.object({}),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async () => {
      try {
        const s = await status();
        // Allow-list, not pass-through: new Core fields never leak to agents by accident.
        return text({
          ok: true,
          connected: s.connected,
          simulated: s.simulated,
          battery_pct: s.device.battery_pct ?? null,
          charging: s.device.charging ?? null,
          wifi_rssi: s.device.wifi_rssi ?? null,
        });
      } catch (e) {
        return failure(e);
      }
    },
  );

  if (readOnly) server.registerTool(
    "piblo_devices",
    {
      title: "Device status",
      description: "Report the Core device status and whether status for the secondary display is available.",
      inputSchema: z.object({}),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async () => {
      try {
        const s = await status();
        return text({ ok: true, devices: [
          { id: "primary", state: !s.connected ? "offline" : s.simulated ? "simulated" : "connected", battery_pct: s.device?.battery_pct ?? null },
          { id: "display", ...await core.displayStatus() },
        ] });
      } catch (e) { return failure(e); }
    },
  );
  return server;
}
