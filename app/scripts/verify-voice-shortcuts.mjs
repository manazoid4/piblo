// Run after npm run build:renderer. Uses an isolated browser and fake IPC, not the running Core.
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../dist/renderer');
const browser = await chromium.launch({ headless: true, executablePath: process.env.PIBLO_TEST_BROWSER || undefined });
try {
  for (const width of [900, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.setDefaultTimeout(5000);
    await page.route('http://piblo.test/**', async (route) => {
      const name = new URL(route.request().url()).pathname.replace(/^\//, '') || 'index.html';
      const file = path.resolve(root, name);
      if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return route.abort();
      const type = name.endsWith('.js') ? 'application/javascript' : name.endsWith('.css') ? 'text/css' : name.endsWith('.html') ? 'text/html' : 'application/octet-stream';
      await route.fulfill({ path: file, contentType: type });
    });
    await page.addInitScript(() => {
      window.savedShortcuts = [];
      const unsub = () => () => {};
      window.piblo = {
        version: 'test', platform: 'win32',
        core: { state: async () => ({ phase: 'running' }), onState: unsub },
        settings: { get: async () => ({ onboardingDone: true, theme: 'light' }), set: async () => ({}) },
        onPairingOpen: unsub, onPairingPending: unsub, onSettingsThemeChanged: unsub,
        api: async (method, apiPath, body) => {
          if (apiPath === '/voice-shortcuts') {
            if (method === 'PUT') window.savedShortcuts = body.shortcuts;
            return { ok: true, data: { shortcuts: window.savedShortcuts } };
          }
          return { ok: true, data: { items: [], requests: [], pending: [] } };
        },
      };
    });
    await page.goto('http://piblo.test/', { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: /^Actions/ }).click();
    await page.getByLabel('Spoken phrase').fill('start work');
    await page.getByLabel('Action 1', { exact: true }).selectOption('desktop');
    await page.getByRole('button', { name: 'Add action', exact: true }).click();
    await page.getByLabel('Action 2', { exact: true }).selectOption('mute');
    await page.getByRole('button', { name: 'Save shortcut', exact: true }).click();
    await page.getByRole('button', { name: 'Edit start work' }).waitFor();
    if (JSON.stringify(await page.evaluate(() => window.savedShortcuts)) !== JSON.stringify([{ phrase: 'start work', actions: ['desktop', 'mute'] }])) throw Error('create failed');
    await page.screenshot({ path: `../piblo-b1-editor-${width}.png`, fullPage: true });
    await page.getByRole('button', { name: 'Edit start work' }).click();
    await page.getByLabel('Action 1', { exact: true }).selectOption('volume_down');
    await page.getByRole('button', { name: 'Remove action 2' }).click();
    await page.getByRole('button', { name: 'Save shortcut', exact: true }).click();
    await page.waitForFunction(() => window.savedShortcuts[0]?.actions.join() === 'volume_down');
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: 'Delete start work' }).click();
    await page.waitForFunction(() => window.savedShortcuts.length === 0);
    await page.close();
  }
  console.log('Voice shortcut create, edit and delete passed at 900px and 1440px');
} finally {
  await browser.close();
}
