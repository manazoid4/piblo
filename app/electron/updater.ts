import type { UpdateInfo, UpdateManager } from 'velopack';
import type { UpdateStatus } from '../shared/types';

const CHECK_EVERY_MS = 4 * 60 * 60 * 1000;

export function createAppUpdater(manager: () => UpdateManager, onChange: (s: UpdateStatus) => void, enabled: boolean, quit: () => void) {
  let status: UpdateStatus = { state: enabled ? 'idle' : 'disabled', progress: 0, version: '' };
  let pending: UpdateInfo | null = null;
  let checking = false;
  const set = (patch: Partial<UpdateStatus>) => { status = { ...status, ...patch }; onChange(status); };
  const check = async () => {
    if (!enabled || checking || pending) return status;
    checking = true;
    try {
      set({ state: 'checking' });
      const updater = manager();
      const info = await updater.checkForUpdatesAsync();
      if (!info) set({ state: 'idle', progress: 0 });
      else {
        set({ state: 'downloading', progress: 0, version: info.TargetFullRelease.Version });
        await updater.downloadUpdateAsync(info, (progress) => set({ state: 'downloading', progress: Math.round(progress) }));
        pending = info;
        set({ state: 'ready', progress: 100 });
      }
    } catch { set({ state: 'error' }); }
    finally { checking = false; }
    return status;
  };
  let timer: ReturnType<typeof setInterval> | undefined;
  return {
    status: () => status,
    check,
    install: () => {
      if (!pending) return false;
      manager().waitExitThenApplyUpdate(pending, true, true);
      quit();
      return true;
    },
    start: () => { if (enabled && !timer) { void check(); timer = setInterval(() => void check(), CHECK_EVERY_MS); } },
    stop: () => { if (timer) clearInterval(timer); timer = undefined; },
  };
}
