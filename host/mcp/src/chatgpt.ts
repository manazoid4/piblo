/**
 * ChatGPT-facing adapter for Piblo's existing MCP tools.
 * The stdio entrypoint stays unchanged; this adds one optional, read-only UI tool.
 * No device commands, secrets, or model calls are made by the UI itself.
 */
import { z } from "zod/v4";
import { CoreClient, type CoreConfig } from "./core.js";
import { createServer } from "./server.js";

export const PIBLO_UI_URI = "ui://piblo/overview.html";
const UI_MIME = "text/html;profile=mcp-app";

/** Self-contained iframe; never contacts Core or exposes its authentication token. */
const OVERVIEW_HTML = String.raw`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:light dark;font:14px system-ui,Segoe UI,sans-serif}
*{box-sizing:border-box}
body{margin:0;padding:12px;background:transparent;color:light-dark(#1d242c,#edf1f4)}
article{max-width:420px;margin:auto;border:1px solid light-dark(#e2e4e5,#3b414b);border-radius:18px;padding:18px;background:light-dark(#fff,#171b23)}
header{display:flex;align-items:center;gap:12px}
.face{width:46px;height:46px;border-radius:14px;background:#ee5a36;color:white;display:grid;place-items:center;font-size:26px}
h1{font-size:18px;margin:0}p{margin:4px 0 0;opacity:.7;font-size:12px}
.status{display:flex;justify-content:space-between;gap:8px;margin-top:16px;padding-top:12px;border-top:1px solid light-dark(#eee,#363c45)}
strong{font-size:13px}span{font-size:13px;overflow-wrap:anywhere}
small{display:block;margin-top:14px;opacity:.6}
</style></head><body><article>
<header><div class="face" aria-hidden="true">●</div><div><h1>Piblo Buddy</h1><p>Physical companion to ChatGPT</p></div></header>
<div class="status"><strong>Connection</strong><span id="connection">Checking…</span></div>
<div class="status"><strong>Device</strong><span id="device">—</span></div>
<div class="status"><strong>Battery</strong><span id="battery">—</span></div>
<small>Device information only. Actions require your permission.</small>
</article>
<script>
(function(){
  function render(value){
    var v=value&&value.structuredContent?value.structuredContent:value;
    if(!v||typeof v!=="object")return;
    document.getElementById("connection").textContent=v.connected?(v.simulated?"Simulator":"Connected"):"Offline";
    document.getElementById("device").textContent=typeof v.client==="string"&&v.client?v.client:"No active device";
    document.getElementById("battery").textContent=Number.isFinite(v.battery_pct)?v.battery_pct+"%":"Not reported";
  }
  render(window.openai&&window.openai.toolOutput);
  window.addEventListener("message",function(ev){
    if(!ev.data||ev.data.method!=="ui/notifications/tool-result")return;
    var p=ev.data.params||{};
    render(p.result||p);
  });
})();
</script></body></html>`;

interface CoreStatus {
  connected: boolean;
  simulated: boolean;
  client: string | null;
  last_seen_ago_s: number | null;
  device?: { battery_pct?: number | null; charging?: boolean | null };
}

/** Create an independently scoped server per remote MCP request. */
export function createChatgptServer(config: CoreConfig) {
  const core = new CoreClient(config);
  const server = createServer(core, config.agent || "chatgpt-piblo");

  server.registerResource(
    "piblo-overview-ui",
    PIBLO_UI_URI,
    { title: "Piblo Buddy", description: "Read-only device connection card", mimeType: UI_MIME },
    async () => ({
      contents: [{
        uri: PIBLO_UI_URI,
        mimeType: UI_MIME,
        text: OVERVIEW_HTML,
        _meta: { ui: { prefersBorder: true, csp: { connectDomains: [], resourceDomains: [] } } },
      }],
    }),
  );

  server.registerTool(
    "piblo_overview",
    {
      title: "Show Piblo Buddy",
      description: "Shows the user's Piblo device connection and battery in a compact ChatGPT card; no writes or network connections from the card.",
      inputSchema: z.object({}),
      annotations: { readOnlyHint: true, openWorldHint: false },
      _meta: { ui: { resourceUri: PIBLO_UI_URI } },
    },
    async () => {
      try {
        const s = await core.get<CoreStatus>("/buddy/status");
        const info = {
          connected: s.connected === true,
          simulated: s.simulated === true,
          client: typeof s.client === "string" ? s.client.slice(0, 80) : null,
          last_seen_seconds_ago: typeof s.last_seen_ago_s === "number" ? s.last_seen_ago_s : null,
          battery_pct: typeof s.device?.battery_pct === "number" ? s.device.battery_pct : null,
        };
        return {
          content: [{ type: "text" as const, text: "Piblo device: " + (info.connected ? info.simulated ? "simulated" : "connected" : "offline") + (info.client ? "; client: " + info.client : "") }],
          structuredContent: info,
        };
      } catch {
        // No sensitive Core errors or bearer tokens are exposed to ChatGPT's UI.
        return {
          content: [{ type: "text" as const, text: "Piblo Core is unavailable or not authenticated." }],
          structuredContent: { connected: false, simulated: false, client: null, last_seen_seconds_ago: null, battery_pct: null },
        };
      }
    },
  );
  return server;
}
