import fs from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = fs.readFileSync(new URL('../src/styles/app.css', import.meta.url), 'utf8');
const app = fs.readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

describe('title-bar Buddy', () => {
  it('is a button that goes Home', () => {
    expect(app).toMatch(/<button[^>]*className="brand"[^>]*aria-label="Piblo: go to Home"[\s\S]*?setSection\('home'\)/);
  });

  it('is not an Electron drag region (drag regions swallow clicks)', () => {
    const rule = css.match(/\n\.brand \{([^}]*)\}/)?.[1] ?? '';
    expect(rule).toContain('-webkit-app-region: no-drag');
    expect(rule).not.toMatch(/-webkit-app-region:\s*drag/);
  });
});
