// Real renderer + IPC; fixture starts expired, then verifies both completion decisions.
import { _electron as electron } from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startMock } from './mock-core.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const mock = await startMock({ port: 18804, pairing: false });
let state = { state: 'awaiting_done', task: 'Finish the test', remaining_s: 0, muted: true };
const decisions = [];
const proxy = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = Buffer.concat(chunks);
  let value;
  if (req.url === '/focus/state') value = state;
  else if (req.url === '/focus/end') {
    decisions.push(JSON.parse(body).done);
    state = { state: 'idle', muted: false, remaining_s: 0 };
    value = { ok: true };
  } else if (req.url === '/focus/start') {
    state = { state: 'running', task: 'Stop the test', remaining_s: 900, muted: true };
    value = state;
  }
  if (value) { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); return; }
  const response = await fetch(`http://127.0.0.1:${mock.port}${req.url}`, { method: req.method, headers: req.headers, ...(body.length ? { body } : {}) });
  res.writeHead(response.status, { 'Content-Type': 'application/json' });
  res.end(Buffer.from(await response.arrayBuffer()));
});
await new Promise(resolve => proxy.listen(18805, '127.0.0.1', resolve));
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-focus-test-'));
fs.writeFileSync(path.join(profile, 'settings.json'), JSON.stringify({ onboardingDone: true }));
let app;
try {
  app = await electron.launch({ args: [root, `--user-data-dir=${profile}`, '--disable-features=CalculateNativeWinOcclusion', '--disable-renderer-backgrounding'], env: { ...process.env, NOD_CORE_DIR: mock.dir, NOD_CORE_PORT: '18805' } });
  const page = await app.firstWindow();
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show());
  await page.getByRole('button', { name: /^Actions/ }).click();
  const card = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Focus sprint' }) });
  await card.getByText('Time is up. Did you finish?').waitFor();
  await card.getByRole('button', { name: 'Finish', exact: true }).click();
  await card.getByRole('button', { name: '15 min', exact: true }).waitFor();
  assert.equal(state.muted, false);
  await card.getByRole('button', { name: '15 min', exact: true }).click();
  await card.getByRole('button', { name: 'Stop', exact: true }).click();
  await card.getByRole('button', { name: '15 min', exact: true }).waitFor();
  assert.equal(state.muted, false);
  assert.deepEqual(decisions, [true, false]);
  console.log('PASS expired Finish sends done:true; Stop sends done:false; both return to idle');
} finally {
  if (app) await app.close();
  proxy.close();
  mock.close();
}
