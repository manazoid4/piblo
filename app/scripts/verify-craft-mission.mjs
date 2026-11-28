import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron } from 'playwright-core';
import { startMock } from './mock-core.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-craft-mission-'));
const proof = 'https://github.com/manazoid4/piblo/pull/206';
const appTask = { id: 't1', agent: 'claude-2', title: 'App: Mission control card', status: 'running', progress: 40, proof };
const coreTask = { id: 't2', agent: 'claude-1', title: 'Core: task proof', status: 'done', progress: 100, proof: '' };
const mock = await startMock({ port: 0, craftTasks: [appTask, coreTask] });
let app;
try {
  fs.writeFileSync(path.join(profile, 'settings.json'), JSON.stringify({ onboardingDone: true, closeToTray: false }));
  app = await electron.launch({
    args: [root, `--user-data-dir=${profile}`],
    env: { ...process.env, NOD_CORE_DIR: mock.dir, NOD_CORE_PORT: String(mock.port), NOD_TOKEN: mock.token },
  });
  const page = await app.firstWindow();
  await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: /^Craft/ }).click({ timeout: 5000 });
  const board = page.getByRole('region', { name: 'Mission control' });
  await board.getByText('Claude 1 · 11% left').waitFor({ timeout: 5000 });
  const tasks = board.getByRole('listitem');
  assert.equal(await tasks.count(), 2);
  const task = tasks.filter({ hasText: appTask.title });
  assert.equal(await task.getByLabel('Progress 40%').count(), 1);
  assert.equal(await task.getByText('Proved', { exact: true }).count(), 0);
  assert.equal(await task.getByRole('link').getAttribute('href'), proof);
  assert.equal(await tasks.filter({ hasText: coreTask.title }).getByText('Done', { exact: true }).count(), 1);
  assert.equal(await page.getByRole('button', { name: 'Open terminal' }).count(), 0);
  appTask.status = 'done';
  appTask.progress = 100;
  await task.locator('.craft-mission-proved').filter({ hasText: 'Proved' }).waitFor({ timeout: 15000 });
  assert.equal(await task.getByLabel('Progress 100%').count(), 1);
  await page.setViewportSize({ width: 390, height: 820 });
  assert.ok((await board.boundingBox()).width >= 300, 'Craft should remain readable at 390 CSS px');
  console.log('Normal Windows Craft shows tasks, progress, usage and live proof. Terminal remains in the pilot.');
} finally {
  if (app) await app.close();
  mock.close();
}
