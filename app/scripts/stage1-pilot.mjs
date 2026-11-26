// Stage 1 baseline: launch the Craft pilot against the mock Core, screenshot Craft, Ask, Usage and Ctrl+K,
// and log which of them actually reach Core. Run after build: node scripts/stage1-pilot.mjs
import { _electron as electron } from 'playwright-core';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startMock } from './mock-core.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'docs', 'screenshots', 'stage1');
fs.mkdirSync(out, { recursive: true });
const m = await startMock({ port: 18790 });
const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-s1-'));
fs.writeFileSync(path.join(userData, 'settings.json'), JSON.stringify({ closeToTray: false, startWithWindows: false, onboardingDone: true }));
const app = await electron.launch({ args: [root, `--user-data-dir=${userData}`, '--craft-terminal-pilot'], env: { ...process.env, NOD_CORE_DIR: m.dir, NOD_CORE_PORT: String(m.port) } });
const page = await app.firstWindow();
const errors = [];
page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
page.on('pageerror', (e) => errors.push(String(e)));
await page.waitForLoadState('domcontentloaded');
const size = async (w, h) => { await app.evaluate(({ BrowserWindow }, s) => BrowserWindow.getAllWindows()[0].setContentSize(s.w, s.h), { w, h }); await page.waitForTimeout(500); };
const shot = (n) => page.screenshot({ path: path.join(out, n + '.png') });
const results = {};
const check = async (name, fn) => { try { await fn(); results[name] = 'ok'; } catch (e) { results[name] = 'FAIL: ' + String(e.message || e).split('\n')[0]; await shot(name + '-fail').catch(() => {}); } };
await size(1280, 800);
await page.waitForTimeout(2500);
await shot('00-start-1280');
await check('craft', async () => {
  await page.locator('nav.rail button', { hasText: 'Craft' }).click({ timeout: 10_000 });
  await page.waitForSelector('h1:has-text("Craft")', { timeout: 10_000 });
  await page.waitForTimeout(800); await shot('01-craft-1280');
  await size(900, 700); await shot('01-craft-900'); await size(1280, 800);
  await page.getByRole('button', { name: 'Open terminal' }).click();
  await page.waitForTimeout(2500); await shot('02-craft-terminal-1280');
  await size(1920, 1080); await page.waitForTimeout(800); await shot('02-craft-terminal-1920'); await size(1280, 800);
});
for (const [label, file] of [['Ask', '03-ask'], ['Usage', '04-usage']]) {
  await check(label.toLowerCase(), async () => {
    await page.locator('nav.rail button', { hasText: label }).first().click({ timeout: 10_000 });
    await page.waitForSelector(`h1:has-text("${label}")`, { timeout: 10_000 });
    await page.waitForTimeout(1500); await shot(file + '-1280');
  });
}
await check('palette', async () => {
  await page.locator('nav.rail button', { hasText: 'Home' }).first().click();
  await page.keyboard.press('Control+k');
  await page.waitForSelector('[role="dialog"][aria-label="Jump anywhere"]', { timeout: 5_000 });
  await page.waitForTimeout(400); await shot('05-palette-1280');
});
const hits = await fetch(`http://localhost:${m.port}/mock/requests`, { headers: { Authorization: `Bearer ${m.token}` } }).then((r) => r.ok ? r.json() : null).catch(() => null);
console.log(JSON.stringify({ results, errors: errors.slice(0, 10), mockRequests: hits }, null, 1));
await app.close(); m.close();
