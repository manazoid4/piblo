// Runs the renderer on the Vite dev server in a real browser (no Electron), with a stub for the
// window.piblo bridge, and checks every page tour: it opens once, is keyboard and screen-reader
// friendly, is remembered, and reopens from Ctrl+K. Saves a screenshot of each step at desktop and
// narrow width to docs/evidence/tours. Run: npm run verify:tours   (set PORT to change 5199)
import { chromium } from 'playwright-core';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'docs', 'evidence', 'tours');
fs.mkdirSync(out, { recursive: true });
const port = process.env.PORT || '5199';
const url = `http://127.0.0.1:${port}/`;
const pages = [['home', 'Home', 1], ['live', 'Live View', 2], ['ask', 'Ask', 3], ['memory', 'Memory', 4], ['actions', 'Actions', 5], ['settings', 'Settings', 6], ['usage', 'Usage', 7]];
const widths = [['desktop', 1280, 800], ['narrow', 480, 800]];
const results = [];
const check = (name, ok, detail = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` - ${detail}` : ''}`); };

const vite = spawn(process.execPath, [path.join(root, 'node_modules', 'vite', 'bin', 'vite.js'), '--port', port, '--strictPort', '--host', '127.0.0.1'], { cwd: root, stdio: 'ignore' });
const stop = () => { try { vite.kill(); } catch {} };
process.on('exit', stop);
for (let i = 0; i < 60; i++) { try { if ((await fetch(url)).ok) break; } catch {} await new Promise((r) => setTimeout(r, 500)); }

const stub = () => {
  const core = { phase: 'running', managed: false, message: '', installDir: '', port: 0, hasToken: true };
  const noop = () => () => {};
  window.piblo = {
    platform: 'win32', version: '0.0.0',
    core: { state: async () => core, ensure: async () => core, restart: async () => core, install: async () => ({ ok: true, message: '' }), openLogs: async () => {}, onState: noop },
    api: async () => ({ ok: true, data: {} }),
    screen: async () => ({ ok: false, code: 'offline', message: 'stub' }), screenPng: async () => ({ ok: false, code: 'offline', message: 'stub' }),
    device: { pair: async () => ({}) },
    update: { status: async () => ({ state: 'idle' }), check: async () => ({ state: 'idle' }), install: async () => {}, onChange: noop },
    audio: async () => ({ ok: false }),
    settings: { get: async () => ({ closeToTray: true, startWithWindows: false, onboardingDone: true, theme: 'light' }), set: async (p) => p },
    env: { get: async () => ({}), set: async () => ({}) }, usage: { get: async () => ({ generatedAt: '', claude: { status: 'not-connected', fiveHour: null, week: { tokens: 0 }, daily: [] }, codex: { status: 'not-connected', today: 0, week: 0, daily: [] }, openrouter: { status: 'not-connected' } }), setKey: async () => ({}) },
    openExternal: async () => {}, saveScreenshot: async () => ({}),
    onPairingPending: noop, onPairingOpen: noop, onWindowVisible: noop, onSettingsThemeChanged: noop,
  };
};

const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome' });
try {
  for (const [wname, w, h] of widths) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    await ctx.addInitScript(stub);
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(url);
    const dialog = page.getByRole('dialog', { name: /./ }).filter({ has: page.locator('#tour-h') });
    for (const [id, label, n] of pages) {
      // Ctrl+number is the app's own page shortcut; the first visit opens the tour by itself.
      await page.keyboard.press(`Control+${n}`);
      await dialog.waitFor({ timeout: 5000 }).catch(async (e) => { console.log('page errors:', errors.join(' | ')); await page.screenshot({ path: path.join(out, `FAIL-${id}-${wname}.png`) }); throw e; });
      const total = Number((await dialog.getByRole('status').innerText()).match(/of (\d+)/)[1]);
      check(`${wname} ${id}: opens by itself with 2-5 steps`, total >= 2 && total <= 5, `${total} steps`);
      check(`${wname} ${id}: focus is inside the dialog`, await page.evaluate(() => !!document.activeElement?.closest('[role=dialog]')));
      for (let s = 1; s <= total; s++) {
        await page.screenshot({ path: path.join(out, `${id}-${wname}-step${s}.png`) });
        if (s < total) await page.keyboard.press(s % 2 ? 'ArrowRight' : 'Enter'); // arrow and button both work
        if (s === 1 && total > 2) { await page.keyboard.press('ArrowLeft'); await page.keyboard.press('ArrowRight'); }
      }
      await dialog.getByRole('button', { name: 'Done' }).click();
      check(`${wname} ${id}: Done closes it`, (await dialog.count()) === 0);
    }
    // Seen is remembered: a second round through every page opens nothing.
    for (const [id, , n] of pages) {
      await page.keyboard.press(`Control+${n}`);
      await page.waitForTimeout(200);
      check(`${wname} ${id}: remembered, no second auto-open`, (await dialog.count()) === 0);
    }
    // Skip with Esc is remembered too (reset first so it opens again).
    await page.evaluate(() => localStorage.clear());
    await page.keyboard.press('Control+2');
    await page.keyboard.press('Control+1');
    await dialog.waitFor();
    await page.keyboard.press('Escape');
    check(`${wname}: Esc skips and marks seen`, (await dialog.count()) === 0 && (await page.evaluate(() => localStorage.getItem('piblo.tours.seen'))).includes('home'));
    // Tab stays inside the dialog.
    await page.keyboard.press('Control+2');
    await dialog.waitFor();
    for (let i = 0; i < 6; i++) await page.keyboard.press('Tab');
    check(`${wname}: Tab stays inside the dialog`, await page.evaluate(() => !!document.activeElement?.closest('[role=dialog]')));
    // While the tour is open, page shortcuts and the palette stay shut (it is a modal).
    await page.keyboard.press('Control+3');
    await page.keyboard.press('Control+k');
    check(`${wname}: modal blocks Ctrl+number and Ctrl+K`, (await dialog.count()) === 1 && (await page.getByPlaceholder('Jump anywhere in Piblo...').count()) === 0);
    await page.keyboard.press('Escape');
    // Reopen from Ctrl+K.
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder('Jump anywhere in Piblo...').fill('tour');
    await page.screenshot({ path: path.join(out, `palette-tour-${wname}.png`) });
    await page.keyboard.press('Enter');
    await dialog.waitFor({ timeout: 3000 });
    check(`${wname}: Ctrl+K "Tour of this page" reopens it`, (await dialog.locator('#tour-h').innerText()).length > 0);
    await page.keyboard.press('Escape');
    // Settings button resets every tour.
    await page.keyboard.press('Control+6');
    await dialog.waitFor();
    await page.keyboard.press('Escape'); // Settings has its own first-visit tour
    await page.getByRole('button', { name: 'Show page tours again' }).click();
    check(`${wname}: Settings reset clears seen`, (await page.evaluate(() => localStorage.getItem('piblo.tours.seen'))) === null);
    check(`${wname}: no page errors`, errors.length === 0, errors.slice(0, 2).join(' | '));
    await ctx.close();
  }
} finally {
  await browser.close();
  stop();
}
const bad = results.filter((r) => !r).length;
console.log(`\n${results.length - bad}/${results.length} checks passed`);
process.exit(bad ? 1 : 0);
