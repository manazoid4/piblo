import { ipcMain, type BrowserWindow, type IpcMainInvokeEvent } from 'electron';
import path from 'node:path';
import os from 'node:os';
import { registerCraftTasks } from './craft-task-ipc';
import type { IPty } from 'node-pty';

export function registerCraftTerminal(window: BrowserWindow, trustedUrl: string) {
  const limit = 128 * 1024;
  let session: { pty: IPty; history: string; end: number; exited: boolean; id: number } | undefined;
  let generation = 0;
  function authorize(event: IpcMainInvokeEvent) {
    const frame = event.senderFrame;
    if (window.isDestroyed() || event.sender !== window.webContents || !frame ||
        frame !== window.webContents.mainFrame || frame.url.split('#')[0] !== trustedUrl) {
      throw new Error('Terminal unavailable for this page.');
    }
  }
  const tasks = registerCraftTasks(authorize);
  function current(id: unknown) {
    if (!session || id !== session.id) throw new Error('Terminal session closed.');
    return session;
  }
  function dimensions(cols: number, rows: number) {
    if (!Number.isInteger(cols) || cols < 2 || cols > 500 || !Number.isInteger(rows) || rows < 1 || rows > 200) {
      throw new Error('Invalid terminal dimensions.');
    }
  }
  function close() {
    const previous = session;
    session = undefined;
    if (previous && !previous.exited) previous.pty.kill();
  }
  ipcMain.handle('craft:open', (event, cols: number, rows: number) => {
    authorize(event);
    dimensions(cols, rows);
    if (!session) {
      const executable = path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
      const native: typeof import('node-pty') = require('node-pty');
      const pty = native.spawn(executable, ['-NoLogo', '-NoProfile'], { cols, rows, cwd: os.homedir(), name: 'xterm-256color' });
      const state = { pty, history: '', end: 0, exited: false, id: ++generation };
      session = state;
      pty.onData(data => {
        state.history = (state.history + data).slice(-limit);
        state.end += data.length;
      });
      pty.onExit(() => { state.exited = true; });
    }
    return { id: session.id, data: session.history, end: session.end };
  });
  ipcMain.handle('craft:read', (event, id, offset: number) => {
    authorize(event);
    const state = current(id);
    if (!Number.isSafeInteger(offset) || offset < 0 || offset > state.end) throw new Error('Invalid terminal offset.');
    const start = state.end - state.history.length;
    return { data: state.history.slice(Math.max(0, offset - start)), end: state.end, exited: state.exited, truncated: offset < start };
  });
  ipcMain.handle('craft:write', (event, id, data: unknown) => {
    authorize(event);
    const state = current(id);
    if (typeof data !== 'string' || data.length > 65536) throw new Error('Invalid terminal input.');
    if (!state.exited) state.pty.write(data);
  });
  ipcMain.handle('craft:resize', (event, id, cols: number, rows: number) => {
    authorize(event);
    dimensions(cols, rows);
    const state = current(id);
    if (!state.exited) state.pty.resize(cols, rows);
  });
  ipcMain.handle('craft:close', (event, id) => { authorize(event); current(id); close(); });
  window.webContents.on('render-process-gone', () => { tasks.cancel(); close(); });
  window.once('closed', () => {
    tasks.dispose();
    for (const channel of ['open', 'read', 'write', 'resize', 'close']) ipcMain.removeHandler('craft:' + channel);
    close();
  });
}
