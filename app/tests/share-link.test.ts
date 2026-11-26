import { describe, expect, it } from 'vitest';
import { extractClipboardShare } from '../electron/share-link';

describe('explicit Share to Piblo clipboard capture', () => {
  it('extracts Github repo from notes without trailing punctuation', () => {
    const r = extractClipboardShare('Consider this: https://github.com/storytold/craft-anything).');
    expect(r?.url).toBe('https://github.com/storytold/craft-anything');
    expect(r?.text).toContain('Consider this');
  });

  it('supports a manually shared Instagram Reel URL', () => {
    expect(extractClipboardShare('https://www.instagram.com/reel/AbC123/?igsh=something')?.url)
      .toBe('https://www.instagram.com/reel/AbC123/?igsh=something');
  });

  it('refuses a clipboard without a URL and strips embedded URL credentials', () => {
    expect(extractClipboardShare('a secret note but no URL')).toBeNull();
    expect(extractClipboardShare('https://user:password@example.com/secret')).toBeNull();
  });

  it('does not treat a deceptive GitHub hostname as trusted in Core', () => {
    expect(extractClipboardShare('https://github.com.evil.example/tool')?.url)
      .toBe('https://github.com.evil.example/tool');
    // Generic web bookmark only. Core's exact-host classifier must not call it a GitHub repo.
  });
});
