// Run after build:main + build:renderer. Exercises Memory through Electron IPC and fixture Core.
import { _electron as electron } from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startMock } from './mock-core.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const mock = await startMock({ port: 18803, pairing: false });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-files-test-'));
const download = path.join(profile, 'download.txt');
const bytes = Buffer.from('Fixture note: hello from home.\nSecond line.\n');
const requests = [];
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/vault' || url.pathname === '/vault/note') {
    requests.push({ path: req.url, auth: req.headers.authorization });
    if (req.headers.authorization !== 'Bearer mock-token-123') { res.writeHead(401).end(); return; }
    res.setHeader('content-type', 'application/json');
    if (url.pathname === '/vault') {
      const files = [{ id: 'note', name: 'note.txt', path: 'notes/note.txt', mime: 'text/plain', size: bytes.length }, { id: 'other', name: 'other.pdf', path: 'other.pdf', mime: 'application/pdf', size: 4 }];
      res.end(JSON.stringify({ enabled: true, files: files.filter(f => f.name.includes(url.searchParams.get('q') || '')), truncated: false }));
    } else res.end(JSON.stringify({ name: 'note.txt', mime: 'text/plain', content_base64: bytes.toString('base64') }));
    return;
  }
  const upstream = http.request({ hostname: '127.0.0.1', port: mock.port, path: req.url, method: req.method, headers: req.headers }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
  upstream.on('error', () => res.writeHead(502).end());
  req.pipe(upstream);
});
await new Promise(resolve => server.listen(18804, '127.0.0.1', resolve));
fs.writeFileSync(path.join(mock.dir, '.env'), 'MAZ_TOKEN=mock-token-123\nMAZ_PORT=18804\nMAZ_DEFAULT_ROUTE=local\nMAZ_GROQ_API_KEY=x\n');
fs.writeFileSync(path.join(profile, 'settings.json'), JSON.stringify({ onboardingDone: true }));
let app;
try {
  app = await electron.launch({ args: [root, `--user-data-dir=${profile}`], env: { ...process.env, NOD_CORE_DIR: mock.dir, NOD_CORE_PORT: '18804' } });
  await app.evaluate(({ session }, savePath) => {
    globalThis.fileDownload = new Promise(resolve => session.defaultSession.once('will-download', (_event, item) => {
      item.setSavePath(savePath);
      item.once('done', (_event, state) => resolve(state));
    }));
  }, download);
  const page = await app.firstWindow();
  await page.getByRole('button', { name: /^Memory/ }).click();
  const vault = page.getByRole('region', { name: 'Fetch from Home' });
  await vault.locator('.title').getByText('other.pdf', { exact: true }).waitFor();
  await vault.getByLabel('Search file names').fill('note');
  await vault.getByRole('button', { name: 'Search' }).click();
  await vault.locator('.title').getByText('other.pdf', { exact: true }).waitFor({ state: 'hidden' });
  assert.equal(await vault.getByText('note.txt', { exact: true }).count(), 1);
  await vault.getByRole('button', { name: 'Fetch' }).click();
  await vault.getByText(bytes.toString(), { exact: true }).waitFor();
  assert.equal(await app.evaluate(() => globalThis.fileDownload), 'completed');
  assert.deepEqual(fs.readFileSync(download), bytes);
  assert.ok(requests.some(r => r.path === '/vault?q=note' && r.auth === 'Bearer mock-token-123'));
  assert.ok(requests.some(r => r.path === '/vault/note' && r.auth === 'Bearer mock-token-123'));
  await page.screenshot({ path: path.join(os.tmpdir(), 'piblo-home-files.png') });
  console.log('PASS authenticated IPC search, filtered list, exact-byte download and text preview');
} finally {
  if (app) await app.close();
  server.close();
  mock.close();
}
