// Proves the Dictation controls in Settings really round-trip through Core: mode/cleanup persist
// across a reload, the dictionary adds and removes, the status line reads Core's numbers, and the
// section fits at 900px. Also saves screenshots. Run: npm run verify:flow
import { _electron as electron } from 'playwright-core';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startMock } from './mock-core.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const shots = process.env.FLOW_SHOTS_DIR || path.join(root, 'artifacts', 'flow');
fs.mkdirSync(shots, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
};

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-flow-'));
fs.writeFileSync(path.join(dir, 'settings.json'), JSON.stringify({ closeToTray: true, startWithWindows: false, onboardingDone: true }));

const m = await startMock({ port: 18792 });
const app = await electron.launch({
  args: [root, `--user-data-dir=${dir}`, '--disable-features=CalculateNativeWinOcclusion', '--disable-renderer-backgrounding'],
  env: { ...process.env, NOD_CORE_DIR: m.dir, NOD_CORE_PORT: String(m.port) },
  timeout: 60_000,
});
const page = await app.firstWindow();
await page.waitForLoadState('domcontentloaded');
await page.waitForSelector('h1:has-text("Home")', { timeout: 30_000 });

await page.keyboard.press('Control+6');
const flow = page.locator('section[aria-labelledby="flow-h"]');
await flow.waitFor({ timeout: 20_000 });
check('Settings has a Dictation section', (await flow.locator('h2').innerText()) === 'Dictation');

const mode = flow.getByRole('group', { name: 'Insert mode' });
const cleanup = flow.getByRole('group', { name: 'Cleanup' });
const pressEnter = flow.getByRole('switch', { name: /Press Enter/i });

check('paste is the default', (await mode.getByRole('button', { name: 'Paste' }).getAttribute('aria-pressed')) === 'true');
check('auto cleanup is the default', (await cleanup.getByRole('button', { name: 'Auto' }).getAttribute('aria-pressed')) === 'true');
check('press Enter starts off', (await pressEnter.isChecked()) === false);
check('cloud cleanup is disabled without a Groq key', await cleanup.getByRole('button', { name: 'Cloud' }).isDisabled());

// Switching to Type must survive a reload (so it really went to Core, not just React state).
await mode.getByRole('button', { name: 'Type' }).click();
await page.waitForFunction(() => [...document.querySelectorAll('[aria-label="Insert mode"] button')].find((b) => b.textContent === 'Type')?.getAttribute('aria-pressed') === 'true', null, { timeout: 10_000 });
check('press Enter is disabled in Type mode', await pressEnter.isDisabled());

await cleanup.getByRole('button', { name: 'On this PC' }).click();
await page.waitForTimeout(600);
await page.reload();
await page.waitForSelector('h1:has-text("Home")', { timeout: 30_000 });
await page.keyboard.press('Control+6');
await flow.waitFor({ timeout: 20_000 });
await page.waitForFunction(() => [...document.querySelectorAll('[aria-label="Insert mode"] button')].find((b) => b.textContent === 'Type')?.getAttribute('aria-pressed') === 'true', null, { timeout: 10_000 });
check('insert mode survives a reload', (await flow.getByRole('group', { name: 'Insert mode' }).getByRole('button', { name: 'Type' }).getAttribute('aria-pressed')) === 'true');
check('cleanup choice survives a reload', (await flow.getByRole('group', { name: 'Cleanup' }).getByRole('button', { name: 'On this PC' }).getAttribute('aria-pressed')) === 'true');

// Put the mode back so the screenshot shows the ordinary paste default.
await flow.getByRole('group', { name: 'Insert mode' }).getByRole('button', { name: 'Paste' }).click();
await page.waitForTimeout(500);

// Dictionary add + remove.
const wordBox = flow.getByLabel('New word');
await wordBox.fill('Hyperspace');
await flow.getByRole('button', { name: 'Add' }).click();
await flow.locator('.tag', { hasText: 'Hyperspace' }).waitFor({ timeout: 10_000 });
check('a new word appears as a chip', await flow.locator('.tag', { hasText: 'Hyperspace' }).isVisible());
await page.reload();
await page.waitForSelector('h1:has-text("Home")', { timeout: 30_000 });
await page.keyboard.press('Control+6');
await flow.waitFor({ timeout: 20_000 });
await flow.locator('.tag', { hasText: 'Hyperspace' }).waitFor({ timeout: 10_000 });
check('the word survives a reload', await flow.locator('.tag', { hasText: 'Hyperspace' }).isVisible());
await flow.locator('.tag', { hasText: 'Hyperspace' }).click();
await flow.locator('.tag', { hasText: 'Hyperspace' }).waitFor({ state: 'detached', timeout: 10_000 });
check('removing a chip deletes the word', (await flow.locator('.tag', { hasText: 'Hyperspace' }).count()) === 0);

// Preferred spellings (Flow aliases, PR #130): a plain word must not flatten a `Term: misheard` line,
// and adding an alias to an existing term merges it instead of duplicating the chip.
await wordBox.fill('Sonarr');
await flow.getByRole('button', { name: 'Add' }).click();
await flow.locator('.tag', { hasText: 'Sonarr' }).waitFor({ timeout: 10_000 });
const rampage = flow.locator('.tag', { hasText: 'Rampage' });
check('an alias line keeps its chip after adding a word', await rampage.isVisible());
check('the chip names the first mishearing', /ram page/.test(await rampage.innerText()), await rampage.innerText());
await wordBox.fill('Rampage: ramp age');
await flow.getByRole('button', { name: 'Add' }).click();
await page.waitForTimeout(700);
check('a new alias merges into the existing term', /\+2/.test(await rampage.innerText()), await rampage.innerText());
check('no duplicate chip for the merged term', (await flow.locator('.tag', { hasText: 'Rampage' }).count()) === 1);
await page.reload();
await page.waitForSelector('h1:has-text("Home")', { timeout: 30_000 });
await page.keyboard.press('Control+6');
await flow.waitFor({ timeout: 20_000 });
await flow.locator('.tag', { hasText: 'Rampage' }).waitFor({ timeout: 10_000 });
check('aliases survive a reload', /\+2/.test(await flow.locator('.tag', { hasText: 'Rampage' }).innerText()));
check('the plain word added alongside survives the reload', await flow.locator('.tag', { hasText: 'Sonarr' }).isVisible());

const statusLine = await flow.getByText(/dictation · average/).innerText();
check('status line reports Core history', /1 dictation · average 640 ms/.test(statusLine), statusLine);

// Screenshots at a normal window and at the narrow minimum.
await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setContentSize(1280, 900));
await page.waitForTimeout(600);
await flow.scrollIntoViewIfNeeded();
await page.screenshot({ path: path.join(shots, 'settings-dictation-1280.png') });

await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setContentSize(900, 700));
await page.waitForTimeout(600);
const wordsRow = flow.locator('.setting', { hasText: 'Words Piblo should get right' });
const label = await wordsRow.locator('strong').evaluate((el) => ({ w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height), rowW: Math.round(el.closest('.setting').getBoundingClientRect().width), growW: Math.round(el.parentElement.getBoundingClientRect().width), kids: [...el.closest('.setting').children].map((c) => `${c.className}:${Math.round(c.getBoundingClientRect().width)}`) }));
check('the words label keeps a usable column', label.w >= 150 && label.h <= 26, `${label.w}x${label.h} row=${label.rowW} grow=${label.growW} [${label.kids.join(' ')}]`);
const overflow = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
check('no horizontal overflow at 900x700', overflow.scroll <= overflow.client + 1, `${overflow.scroll} <= ${overflow.client}`);
await page.screenshot({ path: path.join(shots, 'settings-dictation-900.png') });

console.log(`screenshots: ${shots}`);
await app.close();
m.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);