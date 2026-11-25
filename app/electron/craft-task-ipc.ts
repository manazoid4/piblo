import { app, ipcMain, safeStorage, type IpcMainInvokeEvent } from 'electron';
import { spawnSync } from 'node:child_process';
import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { emptyTask, localTasks, parseTask } from './craft-local';

const format = Buffer.from('PC01');
function protectKey(operation: 'Protect' | 'Unprotect', key: Buffer) {
  const executable = path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  // DPAPI uses the current Windows user. Key bytes travel over the pipes only.
  const script = `$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Security; ` +
    `$bytes=[Convert]::FromBase64String([Console]::In.ReadToEnd()); ` +
    `[Console]::Write([Convert]::ToBase64String([System.Security.Cryptography.ProtectedData]::${operation}($bytes,$null,'CurrentUser')))`;
  const result = spawnSync(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script], {
    input: key.toString('base64'), encoding: 'utf8', timeout: 5000, windowsHide: true,
  });
  if (result.status !== 0 || !result.stdout) throw new Error('Windows task encryption is unavailable.');
  return Buffer.from(result.stdout, 'base64');
}

export function registerCraftTasks(authorize: (event: IpcMainInvokeEvent) => void) {
  const file = path.join(app.getPath('userData'), 'craft-task.enc');
  const keyFile = path.join(app.getPath('userData'), 'craft-task.key');
  let key: Buffer | undefined;
  function taskKey(create: boolean) {
    if (key) return key;
    try {
      const opened = protectKey('Unprotect', fs.readFileSync(keyFile));
      if (opened.length !== 32) throw new Error('Invalid Windows task key.');
      return key = opened;
    } catch (error) {
      if (!create || (error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      const created = randomBytes(32);
      fs.writeFileSync(keyFile + '.tmp', protectKey('Protect', created));
      fs.renameSync(keyFile + '.tmp', keyFile);
      return key = created;
    }
  }
  const tasks = localTasks(fetch, () => {
    let sealed: Buffer;
    try {
      if (fs.statSync(file).size > 512 * 1024) throw new Error('Saved task exceeds the limit.');
      sealed = fs.readFileSync(file);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return emptyTask;
      throw new Error('Unable to read the Craft task file.');
    }
    let plain: string;
    try {
      if (sealed.subarray(0, 4).equals(format)) {
        const decipher = createDecipheriv('aes-256-gcm', taskKey(false), sealed.subarray(4, 16));
        decipher.setAuthTag(sealed.subarray(16, 32));
        plain = Buffer.concat([decipher.update(sealed.subarray(32)), decipher.final()]).toString('utf8');
      } else {
        plain = safeStorage.decryptString(sealed); // recover drafts from the earlier pilot if available
      }
    }
    catch { throw new Error('The Windows profile could not decrypt the Craft task.'); }
    try { return parseTask(plain); }
    catch { throw new Error('The decrypted Craft task has an invalid format.'); }
  }, record => {
    const nonce = randomBytes(12);
    const cipher = createCipheriv('aes-256-gcm', taskKey(true), nonce);
    const encrypted = Buffer.concat([cipher.update(JSON.stringify(record), 'utf8'), cipher.final()]);
    const sealed = Buffer.concat([format, nonce, cipher.getAuthTag(), encrypted]);
    fs.writeFileSync(file + '.tmp', sealed);
    fs.renameSync(file + '.tmp', file);
  });
  ipcMain.handle('craft-task:load', event => { authorize(event); return tasks.load(); });
  ipcMain.handle('craft-task:save', (event, draft) => { authorize(event); tasks.save(draft); });
  ipcMain.handle('craft-task:models', event => { authorize(event); return tasks.models(); });
  ipcMain.handle('craft-task:run', (event, draft) => { authorize(event); return tasks.run(draft); });
  ipcMain.handle('craft-task:cancel', event => { authorize(event); tasks.cancel(); });
  return {
    cancel: tasks.cancel,
    dispose() {
      tasks.cancel();
      for (const name of ['load', 'save', 'models', 'run', 'cancel']) ipcMain.removeHandler('craft-task:' + name);
    },
  };
}
