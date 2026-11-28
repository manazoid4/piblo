import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const appRoot = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const rendererDir = path.join(appRoot, 'src');

/** Everything the person can read in this build: the renderer plus the window's own HTML and app metadata. */
function uiFiles(): { file: string; text: string }[] {
  const out: { file: string; text: string }[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.(tsx?|css|html|svg)$/.test(entry.name)) out.push({ file: full, text: fs.readFileSync(full, 'utf8') });
    }
  };
  walk(rendererDir);
  out.push({ file: path.join(appRoot, 'index.html'), text: fs.readFileSync(path.join(appRoot, 'index.html'), 'utf8') });
  out.push({ file: path.join(appRoot, 'package.json'), text: fs.readFileSync(path.join(appRoot, 'package.json'), 'utf8') });
  return out;
}

// Internal names that must survive because Core reads them, or because they are API paths, not copy.
const ALLOWED = [
  /MAZ_[A-Z][A-Z_]*/g, // Core's own settings keys (never shown)
  /\/core\/cardputer\/[a-z.]*/g, // Core's device routes
  /NOD_[A-Z_]+/g, // dev-only overrides for a local run
];

const BANNED = /\bNod\b|\bMAZ\b|Maz Core|Maz Pocket|Cardputer/i;

function strip(text: string): string {
  return ALLOWED.reduce((acc, re) => acc.replace(re, ''), text);
}

describe('rebrand', () => {
  it('has no user-visible trace of the old names in the app', () => {
    const offenders: string[] = [];
    for (const { file, text } of uiFiles()) {
      strip(text)
        .split(/\r?\n/)
        .forEach((line, i) => {
          if (BANNED.test(line)) offenders.push(`${path.relative(appRoot, file)}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders, `Rename these to Piblo:\n${offenders.join('\n')}`).toEqual([]);
  });

  it('is called Piblo everywhere a person can see it', () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(appRoot, 'package.json'), 'utf8'));
    expect(pkg.productName).toBe('Piblo');
    expect(pkg.build.productName).toBe('Piblo');
    const workflow = fs.readFileSync(path.join(appRoot, '../.github/workflows/app-release.yml'), 'utf8');
    expect(workflow).toContain('--packId PibloApp');
    expect(workflow).toContain('--packTitle Piblo');
    expect(workflow).toContain('PibloApp-win-Setup.exe');
    expect(pkg.description).not.toMatch(BANNED);
    const html = fs.readFileSync(path.join(appRoot, 'index.html'), 'utf8');
    expect(html).toContain('<title>Piblo</title>');
  });

  it('keeps the old names out of the window and tray strings too', () => {
    const main = fs.readFileSync(path.join(appRoot, 'electron', 'main.ts'), 'utf8');
    expect(main).not.toMatch(/\bNod\b/);
    expect(main).toContain("title: 'Piblo'");
  });
});
