// Thin HTTP client for Piblo Core (piblo-proto v1). No orchestration lives here: every decision
// (timeouts, halt, expiry, dedup, storage) is Core's. This file only turns tool calls into /buddy/* requests.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const VERSION = "0.1.0";
const DEFAULT_URL = "http://127.0.0.1:8787";

export interface CoreConfig {
  url: string;
  token: string;
  agent: string;
  displayUrl?: string;
}

/** Pull MAZ_TOKEN / NOD_TOKEN / PIBLO_TOKEN out of a dotenv-style file. Returns "" when absent. */
export function tokenFromEnvFile(path: string): string {
  try {
    for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
      const m = /^\s*(?:export\s+)?(?:PIBLO_TOKEN|NOD_TOKEN|MAZ_TOKEN)\s*=\s*(.*?)\s*$/.exec(line);
      if (m) return m[1].replace(/^(['"])(.*)\1$/, "$2");
    }
  } catch {
    /* file missing or unreadable: fall through to "no token" */
  }
  return "";
}

/**
 * Token lookup, first hit wins: PIBLO_TOKEN / NOD_TOKEN / MAZ_TOKEN env, then a dotenv file
 * (PIBLO_ENV_FILE, else the host/.env that Core itself reads, found relative to this package).
 * shortcut: no OS credential store. Core keeps its token in host/.env and has no keyring dependency,
 * so a keyring lookup here would only add a native module. Upgrade when Core moves the token there.
 */
export function resolveToken(env: NodeJS.ProcessEnv = process.env): string {
  const direct = env.PIBLO_TOKEN || env.NOD_TOKEN || env.MAZ_TOKEN;
  if (direct) return direct;
  const here = dirname(fileURLToPath(import.meta.url));
  const file = env.PIBLO_ENV_FILE || join(here, "..", "..", ".env");
  return tokenFromEnvFile(file);
}

export function cleanAgentName(raw: string | undefined): string {
  // Control characters out, length bounded to Core's 60-char agent field.
  return (raw ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 60);
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): CoreConfig {
  return {
    url: (env.PIBLO_URL || DEFAULT_URL).replace(/\/+$/, ""),
    token: resolveToken(env),
    agent: cleanAgentName(env.PIBLO_AGENT_NAME),
    displayUrl: env.PIBLO_DISPLAY_URL,
  };
}

export class CoreError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status = 0,
  ) {
    super(message);
  }
}

/**
 * The bearer token travels in every request, so plain http is allowed only to this machine or a
 * private LAN / Tailscale address (same rule as the Android app). Anything else must be https.
 */
export function urlAllowed(raw: string): boolean {
  let u: URL;
  try { u = new URL(raw); } catch { return false; }
  if (u.protocol === "https:") return true;
  if (u.protocol !== "http:") return false;
  const h = u.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (h === "localhost" || h === "::1" || h.endsWith(".local")) return true;
  if (!h.includes(".") && !h.includes(":")) return true; // short local machine name
  const o = h.split(".").map(Number);
  if (o.length !== 4 || o.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return false;
  const [a, b] = o;
  return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
    (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127);
}

export class CoreClient {
  constructor(private readonly cfg: CoreConfig) {}

  async displayStatus(): Promise<{ state: string; reason?: string }> {
    if (!this.cfg.displayUrl) return { state: "unknown", reason: "Display address not configured" };
    let url: URL;
    try { url = new URL(this.cfg.displayUrl); } catch { return { state: "error", reason: "Invalid display address" }; }
    // Only literal private IPv4 addresses are allowed; never forward the Core token or follow redirects.
    if (url.protocol !== "http:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash ||
        !/^\d+\.\d+\.\d+\.\d+$/.test(url.hostname) || !urlAllowed(url.toString())) {
      return { state: "error", reason: "Display address must be a private IPv4 HTTP origin" };
    }
    try {
      const response = await fetch(url, { redirect: "error", signal: AbortSignal.timeout(2000) });
      if (!response.ok || !response.body) return { state: "error", reason: "Display portal returned an error" };
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let prefix = "", bytes = 0;
      while (bytes < 16384 && !prefix.includes("Set up this Piblo board.")) {
        const chunk = await reader.read();
        if (chunk.done) break;
        const part = chunk.value.subarray(0, 16384 - bytes);
        bytes += part.length;
        prefix += decoder.decode(part, { stream: true });
      }
      await reader.cancel();
      return prefix.includes("Set up this Piblo board.")
        ? { state: "reachable_unverified", reason: "Portal responded; device identity is not authenticated" }
        : { state: "error", reason: "Unexpected display portal response" };
    } catch { return { state: "offline", reason: "Display portal unreachable" }; }
  }

  private async call<T>(method: "GET" | "POST", path: string, body?: unknown, timeoutMs = 10_000): Promise<T> {
    if (!urlAllowed(this.cfg.url)) {
      throw new CoreError("insecure_url", `Refusing to send the Piblo token to ${this.cfg.url}: use https for anything outside this machine or your home network.`);
    }
    if (!this.cfg.token) {
      throw new CoreError("no_token", "No Piblo token found. Set PIBLO_TOKEN or start Core from a folder with host/.env.");
    }
    let res: Response;
    try {
      res = await fetch(this.cfg.url + path, {
        method,
        headers: {
          Authorization: `Bearer ${this.cfg.token}`,
          "Content-Type": "application/json",
          "X-Piblo-Client": `mcp/${VERSION}`,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch {
      throw new CoreError("core_unreachable", `Piblo Core is not reachable at ${this.cfg.url}. Is it running?`);
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = (await res.json()) as { detail?: unknown };
        if (typeof j.detail === "string") detail = j.detail;
      } catch {
        /* non-JSON error body */
      }
      throw new CoreError(res.status === 401 ? "unauthorized" : "core_error", `Core said ${res.status}: ${detail}`, res.status);
    }
    return (await res.json()) as T;
  }

  get<T>(path: string, timeoutMs?: number) {
    return this.call<T>("GET", path, undefined, timeoutMs);
  }
  post<T>(path: string, body: unknown, timeoutMs?: number) {
    return this.call<T>("POST", path, body, timeoutMs);
  }
}
