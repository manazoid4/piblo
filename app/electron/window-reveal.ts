/**
 * First-paint gate for the main window.
 *
 * The window is created with `show: false`, so it only appears once `ready-to-show` fires. If that
 * event never arrives (the renderer fails to paint, a slow first load) the app runs with an
 * invisible window and nothing on screen. Reveal on the first of `ready-to-show`,
 * `did-finish-load` or a failsafe timeout — exactly once. Tray clicks and second-instance
 * activation keep using showWindow(), which shows the window directly.
 */

export interface RevealableWindow {
  on(event: 'ready-to-show' | 'did-finish-load', listener: () => void): void;
  show(): void;
  focus(): void;
  restore(): void;
  isMinimized(): boolean;
  isDestroyed(): boolean;
}

export interface RevealOptions {
  /** Started minimised to the tray (`--hidden`): the gate stays shut until `present()`. */
  startHidden?: boolean;
  /** Failsafe delay before showing an unpainted window anyway. 0 disables the timer. */
  timeoutMs?: number;
}

export const REVEAL_FAILSAFE_MS = 4000;

export class WindowReveal {
  private revealed: boolean;

  constructor(
    private readonly win: RevealableWindow,
    opts: RevealOptions = {},
  ) {
    this.revealed = opts.startHidden === true;
    if (this.revealed) return;
    // Both events fire on a healthy load; the gate makes the second one a no-op.
    win.on('ready-to-show', () => void this.reveal());
    win.on('did-finish-load', () => void this.reveal());
    if (opts.timeoutMs === 0) return;
    const timer = setTimeout(() => void this.reveal(), opts.timeoutMs ?? REVEAL_FAILSAFE_MS);
    (timer as { unref?: () => void }).unref?.();
  }

  get hasRevealed(): boolean {
    return this.revealed;
  }

  /** Show the window for the first time. Returns true only for the call that made it visible. */
  reveal(): boolean {
    if (this.revealed || this.win.isDestroyed()) return false;
    this.revealed = true;
    if (this.win.isMinimized()) this.win.restore();
    this.win.show();
    this.win.focus();
    return true;
  }
}
