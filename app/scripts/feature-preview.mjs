import { _electron as electron } from 'playwright-core';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
fs.mkdirSync(process.argv[2], { recursive: true });
fs.writeFileSync(path.join(process.argv[2], 'settings.json'), JSON.stringify({ onboardingDone: true, closeToTray: false, startWithWindows: false }));
const app = await electron.launch({ args: [root, `--user-data-dir=${process.argv[2]}`], env: process.env });
try {
  const page = await app.firstWindow();
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show());
  if (process.argv.includes('--check')) {
    await page.getByRole('button', { name: /^Memory/ }).click();
    await page.getByText('Example note.txt', { exact: true }).first().waitFor();
    await page.getByRole('button', { name: /^Show me once/ }).click();
    await page.getByRole('heading', { name: 'Show me once', exact: true }).waitFor();
    console.log('Isolated desktop UI, real authenticated vault, and workflow page passed.');
  } else await new Promise((resolve) => app.on('close', resolve));
} finally {
  await app.close().catch(() => {});
}
