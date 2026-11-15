// Main-process HTTP client for Core. The token never reaches the renderer.
import type { ApiErrorCode, ApiResult } from '../shared/types';

export interface ApiClientDeps {
  baseUrl(): string;
  token(): string;
  /** Called after a 401 so the token can be re-read from .env (Core may have been reinstalled). */
  refreshToken?(): string;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

const FRIENDLY: Record<string, string> = {
  authentication_required: 'Piblo on this PC rejected the saved token. Restart it or run setup again.',
  cardputer_unreachable: 'Your Piblo is not reachable. Check it is on, on Wi-Fi, and paired.',
  approval_expired: 'That approval has already expired.',
  no_fish_key: 'No Fish Audio key is set, so voice search is unavailable.',
  too_many_auth_failures: 'Too many failed attempts. Wait a minute and try again.',
};

export interface CoreHealth {
  name: string;
  version?: string;
  gitSha?: string;
}

export type CompatResult =
  | { status: 'ok'; version?: string; gitSha?: string }
  | { status: 'outdated'; version?: string; gitSha?: string }
  | { status: 'token-mismatch'; version?: string; gitSha?: string }
  | { status: 'unknown'; version?: string; gitSha?: string };

/** Route Core must serve for this app to work; an old host (e.g. Pocket v0.7) answers 404 here. */
export const CAPABILITY_PATH = '/buddy/summary';

export function friendly(detail: unknown, fallback: string): string {
  const obj = detail && typeof detail === 'object' ? (detail as Record<string, unknown>) : null;
  const raw = typeof detail === 'string' ? detail : obj ? String(obj.message ?? obj.error ?? '') : '';
  if (!raw) return fallback;
  const key = Object.keys(FRIENDLY).find((k) => raw.includes(k));
  return key ? FRIENDLY[key] : raw.replace(/_/g, ' ');
}

export function createApiClient(deps: ApiClientDeps) {
  const doFetch = deps.fetch ?? fetch;
  const timeoutMs = deps.timeoutMs ?? 20_000;

  function once(method: string, path: string, body: unknown, token: string): Promise<Response> {
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    let payload: string | undefined;
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
    return doFetch(deps.baseUrl() + path, { method, headers, body: payload, signal: AbortSignal.timeout(path === '/skills/draft/from-teach' ? 120_000 : timeoutMs), cache: 'no-store' });
  }

  async function run<T>(method: string, path: string, body: unknown, raw: boolean): Promise<ApiResult<T>> {
    let res: Response;
    try {
      res = await once(method, path, body, deps.token());
      if (res.status === 401 && deps.refreshToken) {
        const fresh = deps.refreshToken();
        if (fresh && fresh !== deps.token()) res = await once(method, path, body, fresh);
      }
    } catch (e) {
      const timeout = e instanceof Error && (e.name === 'TimeoutError' || e.name === 'AbortError');
      return { ok: false, status: 0, code: timeout ? 'timeout' : 'offline', message: timeout ? 'Core took too long to answer.' : 'Core is not running.' };
    }
    if (res.ok) {
      if (raw) return { ok: true, status: res.status, data: new Uint8Array(await res.arrayBuffer()) as unknown as T };
      const text = await res.text();
      try {
        return { ok: true, status: res.status, data: (text ? JSON.parse(text) : {}) as T };
      } catch {
        return { ok: true, status: res.status, data: { text } as unknown as T };
      }
    }
    let detail: unknown;
    try {
      detail = ((await res.json()) as { detail?: unknown }).detail;
    } catch {
      detail = undefined;
    }
    const code: ApiErrorCode =
      res.status === 401 ? 'locked' : res.status === 503 || String(detail).includes('cardputer') ? 'device-offline' : res.status >= 500 ? 'server' : 'bad-request';
    return { ok: false, status: res.status, code, message: friendly(detail, `Core answered HTTP ${res.status}.`) };
  }

  return {
    request: <T = unknown>(method: string, path: string, body?: unknown) => run<T>(method, path, body, false),
    bytes: (path: string, method = 'GET', body?: unknown) => run<Uint8Array>(method, path, body, true),
    /** Unauthenticated probe: is Piblo's background service listening here? */
    async health(): Promise<CoreHealth | null> {
      try {
        const res = await doFetch(deps.baseUrl() + '/health', { signal: AbortSignal.timeout(2500), cache: 'no-store' });
        if (!res.ok) return null;
        const j = (await res.json()) as { name?: string; version?: unknown; git_sha?: unknown };
        if (typeof j.name !== 'string' || !/core/i.test(j.name)) return null;
        return {
          name: j.name,
          version: typeof j.version === 'string' ? j.version : undefined,
          gitSha: typeof j.git_sha === 'string' ? j.git_sha : undefined,
        };
      } catch {
        return null;
      }
    },
    async probe(): Promise<boolean> {
      return (await this.health()) !== null;
    },
    /**
     * One authenticated call to a route new Cores serve:
     * 404 = Core too old, 401 = saved token not accepted, anything else = fine
     * (503/timeouts are not a compatibility problem, so they never block the app).
     */
    async compat(): Promise<CompatResult> {
      const h = await this.health();
      const id = { version: h?.version, gitSha: h?.gitSha };
      const r = await run<unknown>('GET', CAPABILITY_PATH, undefined, false);
      if (!r.ok && r.status === 404) return { status: 'outdated', ...id };
      if (!r.ok && r.status === 401) return { status: 'token-mismatch', ...id };
      if (r.ok) return { status: 'ok', ...id };
      return { status: 'unknown', ...id };
    },
  };
}
export type ApiClient = ReturnType<typeof createApiClient>;
