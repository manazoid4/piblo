// Screenshots the Craft pilot against a REAL running Core (no mock). Env: NOD_CORE_PORT, NOD_TOKEN, OUT (folder).
import { _electron as electron } from 'playwright-core';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path'; import { fileURLToPath } from 'node:url';
const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = process.env.OUT || path.join(root, 'docs', 'screenshots', 'real-core');
fs.mkdirSync(out, { recursive: true });
const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-real-'));
fs.writeFileSync(path.join(ud, 'settings.json'), JSON.stringify({ closeToTray: false, startWithWindows: false, onboardingDone: true }));
const app = await electron.launch({ args: [root, `--user-data-dir=${ud}`, '--craft-terminal-pilot'], env: { ...process.env, NOD_CORE_DIR: os.tmpdir() } });
const page = await app.firstWindow(); const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); }); page.on('pageerror', (e) => errors.push(String(e)));
await page.waitForLoadState('domcontentloaded');
const size = async (w, h) => { await app.evaluate(({ BrowserWindow }, s) => BrowserWindow.getAllWindows()[0].setContentSize(s.w, s.h), { w, h }); await page.waitForTimeout(600); };
const shot = (n) => page.screenshot({ path: path.join(out, n + '.png') });
const res = {};
const go = async (label, file, wait) => { try { await page.locator('nav.rail button', { hasText: label }).first().click({ timeout: 10000 }); await page.waitForSelector(`h1:has-text("${label}")`, { timeout: 10000 }); await page.waitForTimeout(wait); await shot(file); res[label] = 'ok'; } catch (e) { res[label] = 'FAIL ' + String(e.message).split('\n')[0]; await shot(file + '-fail').catch(() => {}); } };
await size(1280, 800); await page.waitForTimeout(2500);
await go('Craft', '1-mission-real-1280', 4000);
await size(900, 700); await shot('2-mission-real-900'); await size(1280, 800);
res.missionText = await page.locator('section', { hasText: 'Mission control' }).first().innerText().catch(() => 'missing');
await go('Ask', '3-ask-real', 1500); await go('Usage', '4-usage-real', 3000); await go('Home', '5-home-real', 1500);
try { await page.keyboard.press('Control+k'); await page.waitForSelector('[role="dialog"][aria-label="Jump anywhere"]', { timeout: 5000 }); await shot('6-ctrl-k-real'); res.palette = 'ok'; } catch (e) { res.palette = 'FAIL'; }
console.log(JSON.stringify({ res, errors: errors.slice(0, 8) }, null, 1));
await app.close();
