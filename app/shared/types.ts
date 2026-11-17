// Types shared by the main process, preload bridge and renderer.

export type CorePhase =
  | 'checking'
  | 'starting'
  | 'running'
  | 'outdated' // Core answers but lacks routes this app needs (old host)
  | 'token-mismatch' // Core is up but rejects the saved token
  | 'not-installed' // no Core files / venv on this PC
  | 'no-python' // Core files exist but Python 3 is missing, so Core cannot be set up
  | 'needs-setup' // venv exists but no .env / token yet
  | 'failed'; // we tried to start it and it did not come up

export interface CoreState {
  phase: CorePhase;
  /** true when this app launched Core (and will stop it on quit). */
  managed: boolean;
  message: string;
  detail?: string;
  installDir: string;
  port: number;
  hasToken: boolean;
}

export type ApiErrorCode = 'offline' | 'locked' | 'bad-request' | 'device-offline' | 'server' | 'timeout';

export type ApiResult<T = unknown> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; code: ApiErrorCode; message: string };

export type ThemeMode = 'light' | 'dark' | 'system';

export interface AppSettings {
  closeToTray: boolean;
  startWithWindows: boolean;
  onboardingDone: boolean;
  theme: ThemeMode;
}

export interface CoreEnvView {
  route: 'local' | 'auto' | 'cloud' | 'mazlatest' | '';
  ollamaModel: string;
  hasGroqKey: boolean;
  hasOpenRouterKey: boolean;
  hasFishKey: boolean;
  envFile: boolean;
}

export type EnvKey = 'MAZ_DEFAULT_ROUTE' | 'MAZ_OLLAMA_MODEL' | 'MAZ_GROQ_API_KEY' | 'MAZ_OPENROUTER_API_KEY' | 'MAZ_FISH_API_KEY';

/** A device that has asked to connect, waiting for the person to Allow or deny it. */
export interface PairRequest {
  request_id: string;
  code: string;
  name: string;
  device_id: string;
  ip: string;
  age_s: number;
}

export interface SaveResult {
  ok: boolean;
  path?: string;
  message?: string;
}

/** Quiet app updates: downloads in the background, then one "Restart to update". */
export interface UpdateStatus {
  state: 'disabled' | 'idle' | 'checking' | 'downloading' | 'ready' | 'error';
  progress: number;
  version: string;
}

export type DayTotal = { day: string; tokens: number };

export interface UsageSnapshot {
  generatedAt: string;
  claude: { status: 'ok' | 'not-connected'; fiveHour: { tokens: number; start: string; end: string } | null; week: { tokens: number }; daily: DayTotal[] };
  codex: { status: 'ok' | 'not-connected'; today: number; week: number; daily: DayTotal[] };
  openrouter: { status: 'ok' | 'not-connected' | 'error'; totalCredits?: number; totalUsage?: number; message?: string };
}

export interface PibloBridge {
  platform: string;
  version: string;
  core: {
    state(): Promise<CoreState>;
    ensure(): Promise<CoreState>;
    restart(): Promise<CoreState>;
    install(): Promise<{ ok: boolean; message: string }>;
    openLogs(): Promise<void>;
    onState(cb: (s: CoreState) => void): () => void;
  };
  api<T = unknown>(method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<ApiResult<T>>;
  screen(): Promise<ApiResult<Uint8Array>>;
  /** Live View frames: PNG from Core's /core/cardputer/screen.png. */
  screenPng(): Promise<ApiResult<Uint8Array>>;
  /** Saves the frame shown on screen to Pictures\Piblo. */
  saveScreenshot(png: Uint8Array): Promise<SaveResult>;
  /** POST that returns audio bytes (voice preview / say). */
  audio(path: string, body: unknown): Promise<ApiResult<Uint8Array>>;
  settings: {
    get(): Promise<AppSettings>;
    set(patch: Partial<AppSettings>): Promise<AppSettings>;
  };
  env: {
    get(): Promise<CoreEnvView>;
    set(key: EnvKey, value: string): Promise<{ ok: boolean; message: string }>;
  };
  usage: {
    get(): Promise<UsageSnapshot>;
    /** Saves (or clears with '') the OpenRouter key; it is never sent back to the page. */
    setKey(key: string): Promise<boolean>;
  };
  openExternal(url: string): Promise<void>;
  /** Main-process pairing poll (5s cadence) forwards the pending list to the renderer. */
  onPairingPending(cb: (requests: PairRequest[]) => void): () => void;
  /** Fires when a tray notification about a pairing request is clicked. */
  onPairingOpen(cb: () => void): () => void;
    /** Fires when the theme setting changes (from tray or settings page). */
    onSettingsThemeChanged(cb: (theme: ThemeMode) => void): () => void;
    /** Window shown/hidden/minimised, so Live View can stop polling when nobody can see it. */
    onWindowVisible(cb: (visible: boolean) => void): () => void;
  /** USB fallback: send this PC's address and key to Piblo over the cable. */
  device: { pair(): Promise<{ ok: boolean; message: string }> };
  update: {
    status(): Promise<UpdateStatus>;
    check(): Promise<UpdateStatus>;
    /** Restarts into the downloaded version; false when nothing is ready. */
    install(): Promise<boolean>;
    onChange(cb: (s: UpdateStatus) => void): () => void;
  };
}

declare global {
  interface Window {
    piblo: PibloBridge;
  }
}

// Internal identifiers, centralised so the renderer never spells them out (they are not user-visible).
// Core serves these device endpoints; the env keys are what Core reads from its own settings file.
export const CARD = {
  screen: '/core/cardputer/screen',
  key: '/core/cardputer/key',
  action: '/core/cardputer/action',
  device: '/core/device',
} as const;

export const PAIR = {
  pending: '/pair/device/pending',
  approve: (id: string) => `/pair/device/${encodeURIComponent(id)}/approve`,
  deny: (id: string) => `/pair/device/${encodeURIComponent(id)}/deny`,
} as const;

export const ENV_KEY = {
  route: 'MAZ_DEFAULT_ROUTE',
  groq: 'MAZ_GROQ_API_KEY',
  openrouter: 'MAZ_OPENROUTER_API_KEY',
  fish: 'MAZ_FISH_API_KEY',
} as const;
