// Run after build:main + build:renderer. Uses fixture data only; no device or recording.
import { _electron as electron } from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startMock } from './mock-core.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const mock = await startMock({ port: 18821, pairing: false });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-teach-test-'));
const screenshot = path.join(os.tmpdir(), 'piblo-teach-review.png');
let approved = false;
const writes = [];
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/teach' || url.pathname === '/skills' || url.pathname.startsWith('/skills/draft/')) {
    if (req.headers.authorization !== 'Bearer mock-token-123') { res.writeHead(401).end(); return; }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString();
    if (req.method !== 'GET') writes.push({ path: url.pathname, body: body && JSON.parse(body) });
    res.setHeader('content-type', 'application/json');
    if (url.pathname === '/teach') res.end(JSON.stringify({ sessions: [{ session_id: 'teach_abcdefghi', state: 'ready', created_at: 1700000000, transcript: 'Open the project.', recording: 'C:\\fixture\\recording.mp4' }] }));
    else if (url.pathname === '/skills') res.end(JSON.stringify({ skills: approved ? [{ slug: 'open-project', path: 'C:\\fixture\\open-project' }] : [] }));
    else if (url.pathname === '/skills/draft/from-teach') res.end(JSON.stringify({ draft: { draft_id: 'skill_abcdefghi', provider: 'local', skill_md: '# Open project\n\n## Procedure\n1. Open the project.' } }));
    else if (url.pathname === '/skills/draft/skill_abcdefghi/approve') { approved = true; res.end(JSON.stringify({ ok: true })); }
    else res.writeHead(404).end();
    return;
  }
  const upstream = http.request({ hostname: '127.0.0.1', port: mock.port, path: req.url, method: req.method, headers: req.headers }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
  upstream.on('error', () => res.writeHead(502).end());
  req.pipe(upstream);
});
await new Promise(resolve => server.listen(18822, '127.0.0.1', resolve));
fs.writeFileSync(path.join(profile, 'settings.json'), JSON.stringify({ onboardingDone: true }));
let app;
try {
  app = await electron.launch({ args: [root, `--user-data-dir=${profile}`], env: { ...process.env, NOD_CORE_DIR: mock.dir, NOD_CORE_PORT: '18822', NOD_TOKEN: 'mock-token-123' }, ...(process.env.PIBLO_ELECTRON ? { executablePath: process.env.PIBLO_ELECTRON } : {}) });
  const page = await app.firstWindow();
  await page.getByRole('button', { name: /^Show me once/ }).click();
  await page.getByText('Open the project.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Create draft' }).click();
  await page.getByRole('heading', { name: 'Review before saving' }).waitFor();
  assert.match(await page.locator('pre').innerText(), /1\. Open the project/);
  assert.equal(approved, false);
  await page.screenshot({ path: screenshot });
  await page.getByRole('button', { name: 'Discard' }).click();
  assert.equal(approved, false);
  assert.equal(writes.filter(r => r.path.endsWith('/approve')).length, 0);
  await page.getByRole('button', { name: 'Create draft' }).click();
  await page.getByRole('button', { name: 'Save reviewed workflow' }).click();
  await page.getByText('open-project', { exact: true }).waitFor();
  assert.equal(writes.filter(r => r.path.endsWith('/approve')).length, 1);
  assert.deepEqual(writes[0].body, { session_id: 'teach_abcdefghi', route: 'local' });
  assert.ok(writes.every(r => r.path === '/skills/draft/from-teach' || r.path === '/skills/draft/skill_abcdefghi/approve'));
  console.log(`PASS: draft, discard, explicit approval, saved list; screenshot ${screenshot}`);
} finally {
  if (app) await app.close();
  server.close();
  mock.close();
}
