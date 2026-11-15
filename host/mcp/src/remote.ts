/**
 * Optional Streamable HTTP entrypoint for ChatGPT / MCP Inspector.
 * Run separately from the stdio agent bridge. Binds loopback by default.
 * Uses existing CoreClient, MCP tools and physical approval rules.
 */
import { createMcpHandler } from "@modelcontextprotocol/server";
import { timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { Readable } from "node:stream";
import { loadConfig } from "./core.js";
import { createChatgptServer } from "./chatgpt.js";

const MAX_BODY = 256 * 1024;
const host = process.env.PIBLO_REMOTE_HOST || "127.0.0.1";
const port = Number(process.env.PIBLO_REMOTE_PORT || "8791");
const externalToken = process.env.PIBLO_REMOTE_TOKEN || "";
const allowedOrigins = new Set((process.env.PIBLO_REMOTE_ORIGINS || "").split(",").map((o) => o.trim()).filter(Boolean));

if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error("Invalid PIBLO_REMOTE_PORT");
if (externalToken.length < 32) throw new Error("Set a random PIBLO_REMOTE_TOKEN (at least 32 characters) before starting the remote adapter");
if (!["127.0.0.1", "::1", "localhost"].includes(host) && process.env.PIBLO_REMOTE_ALLOW_NONLOCAL !== "1") {
  throw new Error("Non-loopback bind requires explicit PIBLO_REMOTE_ALLOW_NONLOCAL=1 and a trusted TLS/reverse proxy");
}

const config = loadConfig();
const handler = createMcpHandler(
  () => createChatgptServer({ ...config, agent: "chatgpt-piblo" }),
  { legacy: "stateless", maxRequestBodySize: MAX_BODY },
);

function authorized(header: string | undefined): boolean {
  if (!header?.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(header.slice(7), "utf8");
  const expected = Buffer.from(externalToken, "utf8");
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

function answer(res: import("node:http").ServerResponse, status: number, body: string): void {
  res.writeHead(status, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" });
  res.end(body);
}

const httpServer = createServer(async (req, res) => {
  try {
    const path = (req.url || "").split("?")[0];
    if (req.method === "GET" && path === "/healthz") { answer(res, 200, "ok"); return; }
    if (path !== "/mcp") { answer(res, 404, "Not found"); return; }
    if (!["GET", "POST", "DELETE"].includes(req.method || "")) { answer(res, 405, "Method not allowed"); return; }
    // No wildcard browser origins. ChatGPT's server-side MCP calls normally send no Origin.
    if (req.headers.origin && !allowedOrigins.has(req.headers.origin)) {
      answer(res, 403, "Origin denied");
      return;
    }
    if (!authorized(req.headers.authorization)) {
      res.setHeader("WWW-Authenticate", 'Bearer realm="Piblo MCP"');
      answer(res, 401, "Unauthorized");
      return;
    }

    let body: string | undefined;
    if (req.method === "POST") {
      const parts: Buffer[] = [];
      let length = 0;
      for await (const chunk of req) {
        const part = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
        length += part.length;
        if (length > MAX_BODY) { answer(res, 413, "Request too large"); return; }
        parts.push(part);
      }
      body = Buffer.concat(parts).toString("utf8");
    }
    const headers = new Headers();
    for (const [key, value] of Object.entries(req.headers)) {
      if (typeof value === "string") headers.set(key, value);
      else if (Array.isArray(value)) headers.set(key, value.join(", "));
    }
    // The public URL is handled by the trusted tunnel; never trust Host.
    const request = new Request("http://127.0.0.1/mcp", { method: req.method, headers, body });
    const response = await handler.fetch(request);
    res.writeHead(response.status, Object.fromEntries(response.headers.entries()));
    if (!response.body) { res.end(); return; }
    const stream = Readable.fromWeb(response.body as import("node:stream/web").ReadableStream);
    stream.on("error", () => res.destroy());
    res.on("close", () => stream.destroy());
    stream.pipe(res);
  } catch {
    if (!res.headersSent) answer(res, 500, "Piblo MCP request failed");
    else res.destroy();
  }
});
httpServer.requestTimeout = 15_000;
httpServer.headersTimeout = 10_000;
httpServer.listen(port, host, () => {
  const addr = httpServer.address();
  const actualPort = addr && typeof addr !== "string" ? addr.port : port;
  console.error("piblo-mcp remote ready at http://" + host + ":" + actualPort + "/mcp");
});
for (const sig of ["SIGTERM", "SIGINT"] as const) {
  process.on(sig, () => {
    httpServer.close(() => void handler.close().finally(() => process.exit(0)));
  });
}
