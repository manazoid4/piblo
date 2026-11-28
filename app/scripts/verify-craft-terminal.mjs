import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { _electron as electron } from 'playwright-core';
import { startMock } from './mock-core.mjs';

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-craft-'));
fs.writeFileSync(path.join(profile, 'settings.json'), JSON.stringify({ onboardingDone: true }));
const voiceText = 'Check the blue notebook.';
const exerciseVoice = process.env.PIBLO_CRAFT_TEST_VOICE === '1';
const mock = await startMock({ port: 0, pairing: false, craftTranscript: exerciseVoice ? voiceText : '' });
// Opt in on an isolated runner. The application uses Ollama's fixed loopback endpoint.
const exerciseTask = process.env.PIBLO_CRAFT_TEST_TASK === '1';
const taskPrompt = 'Give the blue notebook a short title.';
const taskResult = 'Blue notebook';
let modelServer;
let generations = 0;
let app;
try {
  if (exerciseTask) {
    modelServer = http.createServer(async (req, res) => {
      try {
        res.setHeader('Content-Type', 'application/json');
        if (req.url === '/api/tags') return res.end(JSON.stringify({ models: [{ name: 'test-model', size: 1 }] }));
        if (req.url === '/api/show') return res.end(JSON.stringify({ model_info: { 'general.name': 'test-model' } }));
        assert.equal(req.url, '/api/generate');
        let body = '';
        for await (const chunk of req) body += chunk;
        const request = JSON.parse(body);
        assert.equal(request.prompt, taskPrompt);
        assert.equal(request.model, 'test-model');
        generations++;
        res.end(JSON.stringify({ done: true, response: taskResult }));
      } catch {
        res.statusCode = 400;
        res.end('{}');
      }
    });
    await new Promise((resolve, reject) => {
      modelServer.once('error', reject);
      modelServer.listen(11434, '127.0.0.1', resolve);
    });
  }
  const executablePath = process.env.PIBLO_CRAFT_TEST_EXE;
  const launch = () => electron.launch({
    executablePath,
    args: [...(executablePath ? [] : [root]), `--user-data-dir=${profile}`, '--craft-terminal-pilot', '--disable-features=CalculateNativeWinOcclusion', '--disable-renderer-backgrounding'],
    env: { ...process.env, NOD_CORE_DIR: mock.dir, NOD_CORE_PORT: String(mock.port) },
    timeout: 60_000,
  });
  app = await launch();
  const page = await app.firstWindow();
  const craft = page.getByRole('button', { name: /^Craft/ });
  await craft.click({ timeout: 10_000 });
  await page.getByRole('button', { name: 'Open terminal' }).click();
  const input = page.locator('.xterm-helper-textarea');
  await input.waitFor({ state: 'attached' });
  const getOutput = () => page.locator('.xterm-rows').innerText();
  await input.focus();
  await page.keyboard.type("Write-Output ('PIBLO-' + 'PTY-WORKS')");
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('.xterm-rows')?.textContent.includes('PIBLO-PTY-WORKS'));

  await page.getByRole('button', { name: /^Settings/ }).click();
  await craft.click();
  await page.getByRole('button', { name: 'Open terminal' }).click();
  // xterm only paints while on screen, and the Mission control card now pushes the terminal below the fold.
  await page.getByLabel('PowerShell terminal').scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.querySelector('.xterm-rows')?.textContent.includes('PIBLO-PTY-WORKS'));

  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1040, 680));
  await page.getByRole('button', { name: 'Close terminal' }).click();
  await page.getByRole('button', { name: 'Open terminal' }).click();
  await page.getByRole('button', { name: 'Close terminal' }).waitFor();
  assert.doesNotMatch(await getOutput(), /PIBLO-PTY-WORKS/);
  console.log('PASS: real terminal input and output, reconnect, resize and close');

  if (exerciseTask) {
    await page.getByLabel('What should be built?').fill(taskPrompt);
    await page.getByLabel('Local model').selectOption('test-model');
    await page.getByRole('button', { name: 'Run locally' }).click();
    await page.getByText('Result ready to review.').waitFor();
    await page.getByText(taskResult, { exact: true }).waitFor();
    const sealed = fs.readFileSync(path.join(profile, 'craft-task.enc'));
    assert.ok(sealed.length > 0 && !sealed.includes(taskPrompt) && !sealed.includes(taskResult));
    assert.ok(fs.statSync(path.join(profile, 'craft-task.key')).size > 32);
    await app.close();
    app = await launch();
    const reopened = await app.firstWindow();
    await reopened.getByRole('button', { name: /^Craft/ }).click();
    try {
      await reopened.getByRole('button', { name: 'Continue last task' }).click({ timeout: 10_000 });
    } catch (error) {
      console.error(await reopened.locator('main').innerText());
      throw error;
    }
    await reopened.getByText(taskResult, { exact: true }).waitFor();
    assert.equal(await reopened.getByLabel('What should be built?').inputValue(), taskPrompt);
    assert.equal(generations, 1);
    console.log('PASS: local task, encrypted result, restart and Continue without replay (fixture model)');
  }
  if (exerciseVoice) {
    const current = await app.firstWindow();
    const draft = current.getByLabel('What should be built?');
    await draft.fill('Already typed.');
    await current.getByRole('button', { name: 'Dictate with Piblo' }).click();
    await current.waitForFunction(expected => document.querySelector('#craft-prompt')?.value === expected, 'Already typed.\n' + voiceText);
    assert.equal(generations, exerciseTask ? 1 : 0);
    console.log('PASS: Craft voice appends a fixture transcript without submitting the draft');
  }
} finally {
  await app?.close();
  modelServer?.close();
  mock.close();
  await fs.promises.rm(profile, { recursive: true, force: true, maxRetries: 3 });
}
