// Checks the things a screenshot alone cannot prove: the live frame really decodes, the layout has
// no horizontal overflow at 900px, the polling stops when the window is hidden, and the 503 copy shows.
// Needs the built app. Run: npm run verify:ui
import { _electron as electron } from 'playwright-core';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startMock } from './mock-core.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
};

async function launch(env, onboardingDone) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-verify-'));
  fs.writeFileSync(path.join(dir, 'settings.json'), JSON.stringify({ closeToTray: true, startWithWindows: false, onboardingDone }));
  // Keep actionability checks running when Windows considers the test window occluded.
  const app = await electron.launch({ args: [root, `--user-data-dir=${dir}`, '--disable-features=CalculateNativeWinOcclusion', '--disable-renderer-backgrounding'], env: { ...process.env, ...env }, timeout: 60_000 });
  const page = await app.firstWindow();
  await page.waitForLoadState('domcontentloaded');
  return { app, page };
}

// ---- 1. Onboarding to Live View, with a device waiting to connect ----
{
  const m = await startMock({ port: 18790 });
  const { app, page } = await launch({ NOD_CORE_DIR: m.dir, NOD_CORE_PORT: String(m.port) }, false);

  await page.waitForSelector('.pair-card', { timeout: 30_000 });
  const card = await page.locator('.pair-card').first().innerText();
  check('pairing card wording', /Piblo wants to connect\./.test(card) && /482 913/.test(card), card.replace(/\s+/g, ' ').slice(0, 90));
  check('pairing card offers both choices', (await page.getByRole('button', { name: 'Allow' }).count()) === 1 && (await page.getByRole('button', { name: 'Not mine' }).count()) === 1);
  check('nothing auto-approved', !(await page.getByRole('button', { name: 'Open Live View' }).count()));

  await page.getByRole('button', { name: 'Allow' }).click();
  await page.getByRole('button', { name: 'Open Live View' }).click({ timeout: 15_000 });
  await page.waitForSelector('img.screen');

  const frame = await page.evaluate(async () => {
    const img = document.querySelector('img.screen');
    if (img.decode) await img.decode().catch(() => {});
    return { w: img.naturalWidth, h: img.naturalHeight, src: img.currentSrc.slice(0, 5), render: getComputedStyle(img).imageRendering };
  });
  check('live frame is a real 240x135 image', frame.w === 240 && frame.h === 135, `${frame.w}x${frame.h} ${frame.src}`);
  check('screen is drawn crisply', frame.render === 'pixelated', frame.render);

  const pill = await page.locator('.pill').first().innerText();
  check('device pill says Connected', /Connected/.test(pill), pill.replace(/\s+/g, ' '));

  // Buttons and typing hit Core's routes with the documented bodies.
  await page.getByRole('button', { name: 'Enter' }).click();
  await page.getByLabel('Text to type on Piblo').fill('hello from the pc');
  await page.getByRole('button', { name: 'Send' }).click();
  await page.waitForSelector('.toast', { timeout: 5000 });
  const toastText = await page.locator('.toast').innerText();
  check('typing reached Core', /Sent to Piblo/.test(toastText), toastText);
  check('typing cleared the box after sending', (await page.getByLabel('Text to type on Piblo').inputValue()) === '');

  // 2 fps while visible, silence while hidden.
  const count = () =>
    page.evaluate(async () => {
      const img = document.querySelector('img.screen');
      const before = img.src;
      await new Promise((r) => setTimeout(r, 1200));
      return img.src !== before;
    });
  check('screen updates while visible', await count());

  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].hide());
  const whileHidden = await page.evaluate(async () => {
    const img = document.querySelector('img.screen');
    const before = img.src;
    await new Promise((r) => setTimeout(r, 1500));
    return img.src === before;
  });
  check('polling stops while the window is hidden', whileHidden);
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show());

  // Layout at 900x700.
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setContentSize(900, 700));
  await page.waitForTimeout(500);
  const overflow = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  check('no horizontal overflow at 900x700', overflow.scroll <= overflow.client + 1, `${overflow.scroll} <= ${overflow.client}`);

  // Clicking Buddy in the title bar goes Home (it used to sit in a drag region and swallow the click).
  await page.getByRole('button', { name: 'Piblo: go to Home' }).click();
  const homeShown = await page.getByRole('heading', { name: 'Your Piblo' }).isVisible({ timeout: 5_000 }).catch(() => false);
  const region = await page.evaluate(() => getComputedStyle(document.querySelector('.brand')).getPropertyValue('-webkit-app-region'));
  check('Buddy click opens Home', homeShown && region !== 'drag', `home=${homeShown} region=${region}`);

  await app.close();
  m.close();
}

// ---- 2. Device unreachable: Live View says so in plain words ----
{
  const m = await startMock({ port: 18791, deviceOnline: false });
  const { app, page } = await launch({ NOD_CORE_DIR: m.dir, NOD_CORE_PORT: String(m.port) }, true);
  await page.waitForSelector('h1:has-text("Home")', { timeout: 20_000 });
  await page.keyboard.press('Control+2');
  await page.waitForFunction(() => document.querySelector('.screen-msg')?.textContent?.includes("Piblo isn't reachable"), null, { timeout: 25_000 });
  const text = await page.locator('.screen-msg').innerText();
  check('503 shows the friendly unreachable text', text.includes("Piblo isn't reachable, check it's on and on the same Wi-Fi."), text.replace(/\s+/g, ' ').slice(0, 80));
  await app.close();
  m.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
