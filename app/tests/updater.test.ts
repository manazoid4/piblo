import { describe, expect, it, vi } from 'vitest';
import type { UpdateInfo, UpdateManager } from 'velopack';
import { createAppUpdater } from '../electron/updater';

const info = { TargetFullRelease: { Version: '0.2.1' } } as UpdateInfo;
function fake() {
  return {
    checkForUpdatesAsync: vi.fn(async (): Promise<UpdateInfo | null> => info),
    downloadUpdateAsync: vi.fn(async (_info: UpdateInfo, progress?: (n: number) => void) => { progress?.(42); }),
    waitExitThenApplyUpdate: vi.fn(),
  };
}
describe('app updater', () => {
  it('downloads in the background and applies only on restart', async () => {
    const u = fake(); const quit = vi.fn(); const seen: string[] = [];
    const up = createAppUpdater(() => u as unknown as UpdateManager, s => seen.push(s.state), true, quit);
    expect(up.install()).toBe(false);
    await up.check();
    expect(up.status()).toMatchObject({ state: 'ready', progress: 100, version: '0.2.1' });
    await up.check();
    expect(u.checkForUpdatesAsync).toHaveBeenCalledTimes(1);
    expect(up.install()).toBe(true);
    expect(u.waitExitThenApplyUpdate).toHaveBeenCalledWith(info, true, true);
    expect(quit).toHaveBeenCalledOnce();
    expect(seen).toEqual(['checking', 'downloading', 'downloading', 'ready']);
  });
  it('handles offline, no updates and disabled mode', async () => {
    const u = fake(); u.checkForUpdatesAsync.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(null);
    const up = createAppUpdater(() => u as unknown as UpdateManager, () => {}, true, () => {});
    await up.check(); expect(up.status().state).toBe('error');
    await up.check(); expect(up.status().state).toBe('idle');
    const dev = createAppUpdater(() => u as unknown as UpdateManager, () => {}, false, () => {});
    await dev.check(); expect(dev.status().state).toBe('disabled'); expect(dev.install()).toBe(false);
  });
});
