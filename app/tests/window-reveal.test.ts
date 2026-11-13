import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WindowReveal, type RevealableWindow } from '../electron/window-reveal';

type Handler = () => void;

function fakeWindow(over: Partial<RevealableWindow> = {}) {
  const handlers: Record<string, Handler[]> = {};
  const win = {
    on: (event: string, fn: Handler) => {
      (handlers[event] ??= []).push(fn);
    },
    show: vi.fn(),
    focus: vi.fn(),
    restore: vi.fn(),
    isMinimized: vi.fn(() => false),
    isDestroyed: vi.fn(() => false),
    ...over,
  } as RevealableWindow & {
    show: ReturnType<typeof vi.fn>;
    focus: ReturnType<typeof vi.fn>;
    restore: ReturnType<typeof vi.fn>;
  };
  const emit = (event: string) => handlers[event]?.forEach((fn) => fn());
  return { win, emit, handlers };
}

describe('WindowReveal', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('shows the window once ready-to-show fires', () => {
    const { win, emit } = fakeWindow();
    new WindowReveal(win);
    expect(win.show).not.toHaveBeenCalled();
    emit('ready-to-show');
    expect(win.show).toHaveBeenCalledTimes(1);
    expect(win.focus).toHaveBeenCalledTimes(1);
  });

  it('shows on did-finish-load when ready-to-show never fires', () => {
    const { win, emit } = fakeWindow();
    new WindowReveal(win);
    emit('did-finish-load');
    expect(win.show).toHaveBeenCalledTimes(1);
  });

  it('never shows twice for repeated first-paint events', () => {
    const { win, emit } = fakeWindow();
    new WindowReveal(win);
    emit('did-finish-load');
    emit('ready-to-show');
    emit('did-finish-load');
    expect(win.show).toHaveBeenCalledTimes(1);
  });

  it('shows an unpainted window when the failsafe timer expires', () => {
    const { win } = fakeWindow();
    new WindowReveal(win);
    vi.advanceTimersByTime(4000);
    expect(win.show).toHaveBeenCalledTimes(1);
  });

  it('stays hidden when started with --hidden, however long the renderer takes', () => {
    const { win, emit } = fakeWindow();
    const gate = new WindowReveal(win, { startHidden: true });
    emit('ready-to-show');
    emit('did-finish-load');
    vi.advanceTimersByTime(10_000);
    expect(win.show).not.toHaveBeenCalled();
    expect(gate.hasRevealed).toBe(true);
  });

  it('does not touch a destroyed window', () => {
    const { win, emit } = fakeWindow({ isDestroyed: () => true });
    new WindowReveal(win);
    emit('ready-to-show');
    vi.advanceTimersByTime(4000);
    expect(win.show).not.toHaveBeenCalled();
  });

  it('restores a minimised window before showing it', () => {
    const { win, emit } = fakeWindow({ isMinimized: () => true });
    new WindowReveal(win);
    emit('ready-to-show');
    expect(win.restore).toHaveBeenCalledTimes(1);
    expect(win.show).toHaveBeenCalledTimes(1);
  });
});
