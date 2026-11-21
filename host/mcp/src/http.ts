import { timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { localhostHostValidation, localhostOriginValidation, NodeStreamableHTTPServerTransport } from "@modelcontextprotocol/node";
import { CoreClient } from "./core.js";
import { createServer as createMcpServer } from "./server.js";

export function createHttpServer(core: CoreClient, token: string) {
  if (!token) throw new Error("Core token required for HTTP mode");
  const expected = Buffer.from(`Bearer ${token}`);
  const validateHost = localhostHostValidation();
  const validateOrigin = localhostOriginValidation();
  return createServer(async (req, res) => {
    const received = Buffer.from(req.headers.authorization ?? "");
    if (received.length !== expected.length || !timingSafeEqual(received, expected)) {
      res.writeHead(401, { "Content-Type": "application/json" }).end('{"error":"unauthorized"}');
      return;
    }
    if (!validateHost(req, res) || !validateOrigin(req, res)) return;
    if (req.url !== "/mcp") { res.writeHead(404).end(); return; }
    const server = createMcpServer(core, "", true);
    const transport = new NodeStreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on("close", () => { void transport.close(); void server.close(); });
    try {
      await server.connect(transport);
      await transport.handleRequest(req, res);
    } catch {
      if (!res.headersSent) res.writeHead(500).end(); else res.end();
    }
  });
}
