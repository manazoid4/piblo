// Run after build:main + build:renderer. Tests the real renderer and IPC against mock Core.
import { _electron as electron } from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startMock } from './mock-core.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const mock = await startMock({ port: 18803, pairing: false });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-thought-test-'));
fs.writeFileSync(path.join(profile, 'settings.json'), JSON.stringify({ onboardingDone: true }));
let app;
try {
  app = await electron.launch({ args: [root, `--user-data-dir=${profile}`], env: { ...process.env, NOD_CORE_DIR: mock.dir, NOD_CORE_PORT: String(mock.port) } });
  const page = await app.firstWindow();
  await page.getByRole('button', { name: /^Memory/ }).click();
  const transcript = 'A long captured thought with detail. '.repeat(15) + '\nFINAL SENTENCE: bring the blue notebook. <script>unsafe()</script>';
  await page.getByLabel('New thought').fill(transcript);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  const row = page.locator('.list-item').filter({ hasText: 'A long captured thought' });
  await row.waitFor();
  assert.equal(await row.getByText(/FINAL SENTENCE/).count(), 0, 'list keeps its short summary');
  await row.getByRole('button', { name: 'Read', exact: true }).click();
  await row.getByText(transcript, { exact: true }).waitFor();
  assert.equal(await row.locator('script').count(), 0, 'captured text is never executable markup');
  await row.getByRole('button', { name: 'Close', exact: true }).click();
  assert.equal(await row.getByText(/FINAL SENTENCE/).count(), 0);
  await row.getByRole('button', { name: 'Read', exact: true }).click();
  await row.getByText(transcript, { exact: true }).waitFor();
  await page.screenshot({ path: path.join(os.tmpdir(), 'piblo-read-thought.png') });
  console.log('PASS full transcript, safe literal text, close and reopen');
} finally {
  if (app) await app.close();
  mock.close();
}
