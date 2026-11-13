import { describe, expect, it, vi } from 'vitest';
import type { CoreLocation } from '../electron/core-locator';
import { CoreSidecar, type ChildLike, type SidecarDeps } from '../electron/sidecar';

const ready: CoreLocation = { installDir: 'I', sourceDir: 'S', python: 'py.exe', envFile: 'I\\.env', bundledDir: 'S' };

function make(over: Partial<SidecarDeps> & { loc?: CoreLocation } = {}) {
  const child = {
    pid: 1,
    kill: vi.fn(() => true),
    exitCb: null as null | ((c: number | null) => void),
    once(_e: 'exit', cb: (c: number | null) => void) {
      this.exitCb = cb;
    },
  };
  const spawn = vi.fn((..._a: unknown[]) => child as ChildLike);
  const deps: SidecarDeps = {
    probe: async () => false,
    locate: () => over.loc ?? ready,
    hasToken: () => true,
    port: () => 8787,
    spawn: spawn as unknown as SidecarDeps['spawn'],
    hasSystemPython: () => true,
    sleep: async () => {},
    startTimeoutMs: 2000,
    ...over,
  };
  return { sc: new CoreSidecar(deps), spawn, child };
}

describe('CoreSidecar', () => {
  it('never duplicates a Core that is already running', async () => {
    const { sc, spawn } = make({ probe: async () => true });
    const s = await sc.ensure();
    expect(s.phase).toBe('running');
    expect(s.managed).toBe(false);
    expect(spawn).not.toHaveBeenCalled();
    sc.stop(); // must not kill what it did not start
  });

  it('spawns Core with the right cwd, args and PYTHONPATH, then reports running', async () => {
    let up = false;
    const { sc, spawn } = make({
      probe: async () => up,
      sleep: async () => {
        up = true;
      },
      loc: { ...ready, sourceDir: 'BUNDLED' },
    });
    const s = await sc.ensure();
    expect(s.phase).toBe('running');
    expect(s.managed).toBe(true);
    const [py, args, opts] = spawn.mock.calls[0] as [string, string[], { cwd: string; env: Record<string, string> }];
    expect(py).toBe('py.exe');
    expect(args).toEqual(['-m', 'mazhost']);
    expect(opts.cwd).toBe('I');
    expect(opts.env.PYTHONPATH).toBe('BUNDLED');
  });

  it('recheck() starts Core again when a Core it did not launch disappears', async () => {
    let up = true;
    const { sc, spawn } = make({ probe: async () => up, sleep: async () => { up = true; } });
    expect((await sc.ensure()).managed).toBe(false);
    await sc.recheck(); // still answering: nothing to do
    expect(spawn).not.toHaveBeenCalled();
    up = false; // the external Core was killed
    await sc.recheck();
    expect(spawn).toHaveBeenCalledTimes(1);
    expect(sc.state.phase).toBe('running');
    expect(sc.state.managed).toBe(true);
  });

  it('recheck() kills a hung Core it launched, and its late exit does not clobber the new one', async () => {
    const kids: { kill: ReturnType<typeof vi.fn>; exitCb: null | ((c: number | null) => void) }[] = [];
    const spawn = vi.fn(() => {
      const k = { pid: kids.length + 1, kill: vi.fn(() => true), exitCb: null as null | ((c: number | null) => void), once(_e: 'exit', cb: (c: number | null) => void) { k.exitCb = cb; } };
      kids.push(k);
      return k as ChildLike;
    });
    let up = false;
    const { sc } = make({ probe: async () => up, sleep: async () => { up = true; }, spawn: spawn as unknown as SidecarDeps['spawn'] });
    expect((await sc.ensure()).managed).toBe(true);
    up = false; // first Core hangs: alive but silent
    await sc.recheck();
    await sc.recheck(); // two misses = maybe just busy: leave it alone
    expect(spawn).toHaveBeenCalledTimes(1);
    expect(kids[0].kill).not.toHaveBeenCalled();
    await sc.recheck(); // third miss in a row = hung
    expect(spawn).toHaveBeenCalledTimes(2);
    expect(kids[0].kill).toHaveBeenCalled();
    kids[0].exitCb?.(1); // the hung one finally exits
    expect(sc.state.phase).toBe('running');
    expect(sc.isManaged).toBe(true);
  });

  it('restarts a Core it launched that crashes after being healthy', async () => {
    let up = false;
    const exits: ((c: number | null) => void)[] = [];
    const spawn = vi.fn(() => {
      up = true; // Core answers once launched
      return { pid: 1, kill: () => true, once: (_e: 'exit', cb: (c: number | null) => void) => exits.push(cb) } as ChildLike;
    });
    const { sc } = make({ probe: async () => up, spawn: spawn as unknown as SidecarDeps['spawn'] });
    expect((await sc.ensure()).phase).toBe('running');
    up = false;
    exits[0](1); // killed mid-session
    for (let i = 0; i < 20 && sc.state.phase !== 'running'; i++) await new Promise((r) => setTimeout(r, 0));
    expect(spawn).toHaveBeenCalledTimes(2);
    expect(sc.state.phase).toBe('running');
  });

  it('recheck() leaves non-running states for the owner to retry', async () => {
    const { sc, spawn } = make({ hasToken: () => false });
    expect((await sc.ensure()).phase).toBe('needs-setup');
    await sc.recheck();
    expect(spawn).not.toHaveBeenCalled();
    expect(sc.state.phase).toBe('needs-setup');
  });

  it('shares one attempt between concurrent ensure() calls', async () => {
    let up = false;
    const { sc, spawn } = make({
      probe: async () => up,
      sleep: async () => {
        up = true;
      },
    });
    await Promise.all([sc.ensure(), sc.ensure(), sc.ensure()]);
    expect(spawn).toHaveBeenCalledTimes(1);
  });

  it('explains missing Python instead of failing silently', async () => {
    const { sc, spawn } = make({ loc: { ...ready, python: null }, hasSystemPython: () => false });
    const s = await sc.ensure();
    expect(s.phase).toBe('no-python');
    expect(s.message).toMatch(/Python/);
    expect(spawn).not.toHaveBeenCalled();
  });

  it('offers setup when Core is not installed but Python exists', async () => {
    const { sc } = make({ loc: { ...ready, python: null, sourceDir: null } });
    expect((await sc.ensure()).phase).toBe('not-installed');
  });

  it('asks for setup when the token or .env is missing', async () => {
    const { sc } = make({ hasToken: () => false });
    expect((await sc.ensure()).phase).toBe('needs-setup');
  });

  it('fails clearly, with the log tail, when Core never answers', async () => {
    const { sc, child } = make({ startTimeoutMs: 1000, logTail: () => 'ModuleNotFoundError: fastapi' });
    const s = await sc.ensure();
    expect(s.phase).toBe('failed');
    expect(s.detail).toContain('fastapi');
    expect(child.kill).toHaveBeenCalled();
  });

  it('detects a crash right after launch', async () => {
    const holder: { exit?: () => void } = {};
    const { sc, child } = make({
      startTimeoutMs: 5000,
      sleep: async () => {
        holder.exit?.();
      },
    });
    holder.exit = () => child.exitCb?.(1);
    const s = await sc.ensure();
    expect(s.phase).toBe('failed');
  });

  it('stop() kills only the child it launched', async () => {
    let up = false;
    const { sc, child } = make({
      probe: async () => up,
      sleep: async () => {
        up = true;
      },
    });
    await sc.ensure();
    sc.stop();
    expect(child.kill).toHaveBeenCalledTimes(1);
    expect(sc.isManaged).toBe(false);
  });

  it.each([
    ['ok', 'running'],
    ['outdated', 'outdated'],
    ['token-mismatch', 'token-mismatch'],
    ['unknown', 'running'],
  ] as const)('compat %s -> phase %s', async (status, phase) => {
    const { sc } = make({ probe: async () => true, compat: async () => ({ status, version: '0.7.0' }) });
    const s = await sc.ensure();
    expect(s.phase).toBe(phase);
    if (status === 'outdated') expect(s.message).toBe('Core needs updating.');
    if (status === 'token-mismatch') expect(s.message).toMatch(/token/);
  });

  it('an old Core that comes up after our spawn is also gated', async () => {
    let up = false;
    const { sc } = make({ probe: async () => up, sleep: async () => { up = true; }, compat: async () => ({ status: 'outdated' }) });
    expect((await sc.ensure()).phase).toBe('outdated');
  });
});
