import { describe, expect, it } from 'vitest';
import { describeUpdate } from '../src/lib/useUpdate';
import type { UpdateStatus } from '../shared/types';

const st = (state: UpdateStatus['state'], version = '', progress = 0): UpdateStatus => ({ state, version, progress });

describe('describeUpdate', () => {
  it('never leaves the user without words', () => {
    expect(describeUpdate(null)).toMatch(/background/i);
    expect(describeUpdate(st('idle'))).toMatch(/latest/i);
    expect(describeUpdate(st('checking'))).toMatch(/checking/i);
    expect(describeUpdate(st('downloading', '0.2.0-beta.3', 42))).toContain('42%');
    expect(describeUpdate(st('ready', '0.2.0-beta.3'))).toContain('0.2.0-beta.3');
    expect(describeUpdate(st('error'))).toMatch(/try again later/i);
    expect(describeUpdate(st('disabled'))).toMatch(/development build/i);
  });
});
