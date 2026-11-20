import { describe, expect, it } from 'vitest';
import { bodyDoubleLine } from '../src/components/BuddySection';

const base = { enabled: true, mode: 'voice' as const, interval_min: 25, break_every: 4 };

describe('bodyDoubleLine', () => {
  it('says off when disabled', () => expect(bodyDoubleLine({ ...base, enabled: false })).toBe('Off.'));
  it('describes each mode', () => {
    expect(bodyDoubleLine(base)).toContain('every 25 minutes');
    expect(bodyDoubleLine({ ...base, mode: 'glance' })).toContain('never sent online');
    expect(bodyDoubleLine({ ...base, mode: 'silent' })).toContain('every 100 minutes');
  });
});
