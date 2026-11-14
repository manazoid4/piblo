import { app, BrowserWindow, Menu, Notification, Tray, ipcMain, nativeImage, nativeTheme, shell } from 'electron';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import type { AppSettings, EnvKey, PairRequest, ThemeMode } from '../shared/types';
import { CARD, PAIR } from '../shared/types';
import { createApiClient } from './api-client';
import { discoverPort, discoverToken, locateCore, type FsLike } from './core-locator';
import { applyEnvEdit, validateEnvEdit, viewEnv } from './env-file';
import { isSafeTaskName, planInstall } from './install-plan';
import { CoreSidecar } from './sidecar';
import { autoUpdater } from 'electron-updater';
import { createAppUpdater, type UpdaterLike } from './updater';

const realFs: FsLike = {
  exists: (p) => fs.existsSync(p),
  read: (p) => {
    try {
      return fs.readFileSync(p, 'utf8');
    } catch {
      return null;
    }
  },
};

const isDev = !app.isPackaged;
const startHidden = process.argv.includes('--hidden');
const colors = {
  light: { bg: '#fff4e0', fg: '#2a1a12' },
  dark: { bg: '#1d1b19', fg: '#f1ebe2' },
};

// ---- settings (tiny JSON in userData) ----
const settingsFile = () => path.join(app.getPath('userData'), 'settings.json');
const defaults: AppSettings = { closeToTray: true, startWithWindows: false, onboardingDone: false, theme: 'system' };
function loadSettings(): AppSettings {
  try {
    const loaded = JSON.parse(fs.readFileSync(settingsFile(), 'utf8')) as Partial<AppSettings>;
    return { ...defaults, ...loaded, theme: loaded.theme ?? 'system' };
  } catch {
    return { ...defaults };
  }
}
let settings = defaults;

// ---- Core wiring ----
const locate = () =>
  locateCore({ env: process.env, resourcesPath: process.resourcesPath ?? '', devHostDir: path.resolve(__dirname, '..', '..', '..', 'host'), fs: realFs });
const port = () => discoverPort(locate(), realFs, process.env);
const token = () => discoverToken(locate(), realFs, process.env);
const api = createApiClient({ baseUrl: () => `http://127.0.0.1:${port()}`, token, refreshToken: token });

const logFile = () => path.join(locate().installDir, 'logs', 'core.log');
function logTail(): string {
  try {
    return fs.readFileSync(logFile(), 'utf8').trim().split(/\r?\n/).slice(-4).join(' | ').slice(-400);
  } catch {
    return '';
  }
}

const sidecar = new CoreSidecar({
  probe: () => api.probe(),
  compat: () => api.compat(),
  locate,
  hasToken: () => token() !== '',
  port,
  spawn: (python, args, opts) => spawn(python, args, { ...opts, windowsHide: true, stdio: 'ignore' }),
  hasSystemPython: () => {
    for (const [cmd, args] of [['py', ['-3', '-c', 'print(1)']], ['python', ['-c', 'print(1)']]] as const) {
      const r = spawnSync(cmd, [...args], { timeout: 4000, windowsHide: true, encoding: 'utf8' });
      if (r.status === 0 && r.stdout.trim() === '1') return true;
    }
    return false;
  },
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  logTail,
  baseEnv: process.env,
});

// ---- window + tray ----
let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;

function theme() {
  return nativeTheme.shouldUseDarkColors ? colors.dark : colors.light;
}

function applyTheme(mode: ThemeMode) {
  if (mode === 'system') {
    nativeTheme.themeSource = 'system';
  } else {
    nativeTheme.themeSource = mode;
  }
  if (win) {
    const t = theme();
    win.setTitleBarOverlay({ color: t.bg, symbolColor: t.fg, height: 44 });
  }
}

nativeTheme.on('updated', () => {
  if (win && settings.theme === 'system') {
    const t = theme();
    win.setTitleBarOverlay({ color: t.bg, symbolColor: t.fg, height: 44 });
  }
});

function createWindow(): BrowserWindow {
  const t = theme();
  const w = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 960,
    minHeight: 620,
    show: false,
    backgroundColor: t.bg,
    title: 'Piblo',
    icon: path.join(__dirname, '..', '..', 'build', 'icon.png'),
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: t.bg, symbolColor: t.fg, height: 44 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [`--piblo-version=${app.getVersion()}`],
    },
  });
  w.setMenuBarVisibility(false);
  w.once('ready-to-show', () => {
    if (!startHidden) w.show();
  });
  w.on('close', (e) => {
    if (!quitting && settings.closeToTray) {
      e.preventDefault();
      w.hide();
    }
  });
  // document.hidden stays false in this Electron build when the window is hidden, so tell the
  // renderer directly. Live View stops asking Core for frames while this says false.
  const sendVisible = () => w.webContents.send('window:visible', w.isVisible() && !w.isMinimized());
  w.on('show', sendVisible);
  w.on('hide', sendVisible);
  w.on('minimize', sendVisible);
  w.on('restore', sendVisible);
  w.on('focus', sendVisible);
  w.webContents.on('did-finish-load', sendVisible);
  w.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  w.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith('http://localhost:5173') && !url.startsWith('file://')) e.preventDefault();
  });
  const dev = process.env.NOD_DEV_URL;
  if (dev) void w.loadURL(dev);
  else void w.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
  return w;
}

function showWindow() {
  if (!win || win.isDestroyed()) win = createWindow();
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

function createTray() {
  const img = nativeImage.createFromPath(path.join(__dirname, '..', '..', 'build', 'tray.png'));
  tray = new Tray(img);
  tray.setToolTip('Piblo');
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Open Piblo', click: showWindow },
      { type: 'separator' },
      {
        label: 'Theme',
        submenu: [
          { label: 'Light', type: 'radio', checked: settings.theme === 'light', click: () => applyThemeChange('light') },
          { label: 'Dark', type: 'radio', checked: settings.theme === 'dark', click: () => applyThemeChange('dark') },
          { label: 'System', type: 'radio', checked: settings.theme === 'system', click: () => applyThemeChange('system') },
        ],
      },
      { type: 'separator' },
      { label: 'Quit', click: () => { quitting = true; app.quit(); } },
    ]),
  );
  tray.on('click', showWindow);
}

function applyThemeChange(mode: ThemeMode) {
  settings = { ...settings, theme: mode };
  fs.mkdirSync(path.dirname(settingsFile()), { recursive: true });
  fs.writeFileSync(settingsFile(), JSON.stringify(settings));
  applyTheme(mode);
  win?.webContents.send('settings:theme-changed', mode);
}

function applyLoginItem() {
  if (isDev) return;
  app.setLoginItemSettings({ openAtLogin: settings.startWithWindows, args: ['--hidden'] });
}

// ---- updates ----
const appUpdater = createAppUpdater(autoUpdater as unknown as UpdaterLike, (s) => win?.webContents.send('update:changed', s), !isDev);

// ---- IPC ----
function setupIpc() {
  ipcMain.handle('update:status', () => appUpdater.status());
  ipcMain.handle('update:check', () => appUpdater.check());
  ipcMain.handle('update:install', async () => {
    if (appUpdater.status().state !== 'ready') return false;
    quitting = true;
    sidecar.stop();
    await new Promise((r) => setTimeout(r, 800)); // let a Core this app started release its files
    try {
      return appUpdater.install();
    } catch {
      quitting = false;
      void sidecar.ensure();
      return false;
    }
  });
  ipcMain.handle('core:state', () => sidecar.state);
  ipcMain.handle('core:ensure', () => sidecar.ensure());
  ipcMain.handle('core:restart', () => sidecar.restart());
  ipcMain.handle('core:open-logs', async () => {
    const f = logFile();
    if (fs.existsSync(f)) await shell.openPath(f);
    else await shell.openPath(locate().installDir);
  });
  ipcMain.handle('core:install', () => installCore());
  ipcMain.handle('api:request', (_e, method: string, p: string, body?: unknown) => {
    if (typeof p !== 'string' || !p.startsWith('/') || p.includes('..')) return { ok: false, status: 0, code: 'bad-request', message: 'Bad path.' };
    return api.request(method, p, body);
  });
  ipcMain.handle('api:screen', () => api.bytes(CARD.screen));
  ipcMain.handle('api:screen-png', () => api.bytes(CARD.screen + '.png'));
  ipcMain.handle('screen:save', (_e, png: Uint8Array) => {
    try {
      const bytes = Buffer.from(png);
      if (bytes.length < 4 || bytes[0] !== 0x89 || bytes[1] !== 0x50) return { ok: false, message: 'Not a PNG image.' };
      const dir = path.join(app.getPath('pictures'), 'Piblo');
      fs.mkdirSync(dir, { recursive: true });
      const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const file = path.join(dir, `piblo-${stamp}.png`);
      fs.writeFileSync(file, bytes);
      return { ok: true, path: file };
    } catch (e) {
      return { ok: false, message: (e as Error).message };
    }
  });
  ipcMain.handle('api:audio', (_e, p: string, body: unknown) => (p === '/voices/preview' || p === '/say' ? api.bytes(p, 'POST', body) : { ok: false, status: 0, code: 'bad-request', message: 'Not allowed.' }));
  ipcMain.handle('device:pair', () => pairDevice());
  ipcMain.handle('settings:get', () => settings);
  ipcMain.handle('settings:set', (_e, patch: Partial<AppSettings>) => {
        const next = { ...settings };
        const themeVal = patch.theme;
        if (typeof themeVal === 'string') {
          next.theme = themeVal as ThemeMode;
        }
        if (typeof patch.closeToTray === 'boolean') next.closeToTray = patch.closeToTray;
        if (typeof patch.startWithWindows === 'boolean') next.startWithWindows = patch.startWithWindows;
        if (typeof patch.onboardingDone === 'boolean') next.onboardingDone = patch.onboardingDone;
        settings = next;
        fs.mkdirSync(path.dirname(settingsFile()), { recursive: true });
        fs.writeFileSync(settingsFile(), JSON.stringify(settings));
        applyLoginItem();
        applyTheme(settings.theme);
        return settings;
      });
  ipcMain.handle('env:get', () => viewEnv(locate().envFile ? realFs.read(locate().envFile!) : null));
  ipcMain.handle('env:set', (_e, key: EnvKey, value: string) => {
    const loc = locate();
    const bad = validateEnvEdit(key, value);
    if (bad) return { ok: false, message: bad };
    if (!loc.envFile) return { ok: false, message: 'Core has no settings file yet. Set up Core first.' };
    fs.copyFileSync(loc.envFile, loc.envFile + '.nod-backup');
    fs.writeFileSync(loc.envFile, applyEnvEdit(realFs.read(loc.envFile) ?? '', key, value));
    return { ok: true, message: sidecar.isManaged ? 'Saved. Restart Core to apply.' : 'Saved. Restart Core (or sign out and in) to apply.' };
  });
  ipcMain.handle('shell:open-external', (_e, url: string) => (/^https?:\/\//.test(url) ? shell.openExternal(url) : undefined));
}

/** Names of scheduled tasks on this PC (empty if the query fails). */
function listScheduledTasks(): string[] {
  const r = spawnSync('powershell.exe', ['-NoLogo', '-NoProfile', '-Command', '(Get-ScheduledTask -ErrorAction SilentlyContinue).TaskName'], {
    timeout: 10_000,
    windowsHide: true,
    encoding: 'utf8',
  });
  return r.status === 0 ? r.stdout.split(/[\r\n]+/).filter(Boolean) : [];
}

/** Copy the bundled host into the install dir (never touching .env or .venv) and run the existing installer in a visible console. */
async function installCore(): Promise<{ ok: boolean; message: string }> {
  const loc = locate();
  if (!loc.bundledDir) return { ok: false, message: 'This build does not include the Core files.' };
  try {
    fs.mkdirSync(loc.installDir, { recursive: true });
    if (path.resolve(loc.bundledDir) !== path.resolve(loc.installDir)) {
      fs.cpSync(loc.bundledDir, loc.installDir, {
        recursive: true,
        filter: (src) => !/[\\/](\.env|\.venv|__pycache__|tests|logs|data)([\\/]|$)/.test(src.slice(loc.bundledDir!.length)),
      });
      const ver = path.join(loc.bundledDir, 'VERSION');
      if (fs.existsSync(ver)) fs.copyFileSync(ver, path.join(loc.installDir, 'VERSION'));
    }
    // An existing Core scheduled task already keeps Core alive: restart it instead of adding a second (Startup-folder) autostart.
    const plan = planInstall(listScheduledTasks());
    if (plan.kind === 'reuse-task' && isSafeTaskName(plan.task)) {
      sidecar.stop();
      spawnSync('schtasks.exe', ['/End', '/TN', plan.task], { timeout: 10_000, windowsHide: true });
      const run = spawnSync('schtasks.exe', ['/Run', '/TN', plan.task], { timeout: 10_000, windowsHide: true, encoding: 'utf8' });
      if (run.status !== 0) return { ok: false, message: `Core files were updated, but the "${plan.task}" scheduled task would not start. Start it from Task Scheduler.` };
      return { ok: true, message: `Core files updated and the existing "${plan.task}" task restarted. Choose Check again in a few seconds.` };
    }
    const ps1 = path.join(loc.installDir, 'install-core.ps1');
    spawn('powershell.exe', ['-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', ps1, '-NoBrowser'], {
      cwd: loc.installDir,
      detached: true,
      stdio: 'ignore',
    }).unref();
    return { ok: true, message: 'Setup is running in a separate window. When it says READY, come back and choose Check again.' };
  } catch (e) {
    return { ok: false, message: `Setup could not start: ${(e as Error).message}` };
  }
}

/** USB fallback: the existing pair.ps1 sends Core's address and token to Piblo over the cable. */
function pairDevice(): Promise<{ ok: boolean; message: string }> {
  const loc = locate();
  const ps1 = path.join(loc.installDir, 'pair.ps1');
  if (!fs.existsSync(ps1)) return Promise.resolve({ ok: false, message: 'Piblo on this PC is not set up yet, so there is nothing to send.' });
  return new Promise((resolve) => {
    const child = spawn('powershell.exe', ['-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps1, '-Quiet'], { cwd: loc.installDir, windowsHide: true });
    let out = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (out += d));
    const timer = setTimeout(() => child.kill(), 30_000);
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) return resolve({ ok: true, message: 'Your Piblo is connected to this PC.' });
      const text = out.trim();
      resolve({
        ok: false,
        message: /not found over USB/i.test(text)
          ? 'Plug your Piblo into this PC with a USB cable, then try again.'
          : text.split(/\r?\n/).filter(Boolean).slice(-1)[0]?.slice(0, 220) || 'That did not finish.',
      });
    });
    child.on('error', (e) => resolve({ ok: false, message: e.message }));
  });
}

/** Group a 6-digit pairing code as "482 913". */
function formatPairCode(code: string): string {
  const c = code.replace(/\s/g, '');
  return /^\d{6}$/.test(c) ? `${c.slice(0, 3)} ${c.slice(3)}` : code;
}

/** Poll for pairing requests at the tray cadence (5 s) and notify when the window is hidden. */
const pairingSeen = new Set<string>();
async function pollPairing() {
  if (sidecar.state.phase !== 'running') return;
  const r = await api.request<{ requests?: PairRequest[] }>('GET', PAIR.pending);
  const requests = r.ok ? (r.data?.requests ?? []) : [];
  const fresh = requests.filter((x) => !pairingSeen.has(x.request_id));
  for (const x of fresh) pairingSeen.add(x.request_id);
  if (fresh.length && !(win?.isVisible() ?? false) && Notification.isSupported()) {
    const n = new Notification({
      title: 'Piblo wants to connect',
      body: `Check Piblo shows ${formatPairCode(fresh[0].code)}. Open Piblo to allow or deny.`,
    });
    n.on('click', () => {
      showWindow();
      win?.webContents.send('pairing:open');
    });
    n.show();
  }
  win?.webContents.send('pairing:pending', requests);
}

// ---- lifecycle ----
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // Opening Piblo again is the owner's natural "try again", so retry Core if it is not up.
  app.on('second-instance', () => {
    showWindow();
    void (sidecar.state.phase === 'running' ? sidecar.recheck() : sidecar.ensure());
  });
  app.setAppUserModelId('uk.piblo.app');
  void app.whenReady().then(() => {
      settings = loadSettings();
      Menu.setApplicationMenu(null);
      setupIpc();
      sidecar.on('state', (s) => win?.webContents.send('core:state-changed', s));
      applyTheme(settings.theme);
      win = createWindow();
      createTray();
      applyLoginItem();
      void sidecar.ensure();
      setInterval(() => {
        void sidecar.recheck();
        void pollPairing();
      }, 5000);
      appUpdater.start();
    });
  app.on('before-quit', () => {
    quitting = true;
    sidecar.stop();
  });
  app.on('window-all-closed', () => {
    if (quitting || !settings.closeToTray) app.quit();
  });
}
