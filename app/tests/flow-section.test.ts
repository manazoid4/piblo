import { describe, expect, it } from 'vitest';
import { flowStatusLine } from '../src/components/FlowSection';

describe('flowStatusLine', () => {
  it('explains an unreadable history', () => {
    expect(flowStatusLine(null, 'offline')).toBe('Could not read Flow history: offline');
  });

  it('stays calm before the first dictation', () => {
    expect(flowStatusLine(null, null)).toBe('No dictations yet.');
    expect(flowStatusLine({ count: 0, avg_total_ms: 0, p95_total_ms: 0, recent: [], errors: [] }, null)).toBe(
      'No dictations yet.',
    );
  });

  it('counts one dictation in the singular', () => {
    expect(
      flowStatusLine({ count: 1, avg_total_ms: 640, p95_total_ms: 640, recent: [{ chars: 12 }], errors: [] }, null),
    ).toBe('1 dictation · average 640 ms');
  });

  it('surfaces the most recent problem', () => {
    expect(
      flowStatusLine(
        { count: 3, avg_total_ms: 820, p95_total_ms: 990, recent: [{ chars: 9 }], errors: ['mic busy', 'no speech'] },
        null,
      ),
    ).toBe('3 dictations · average 820 ms · last problem: no speech');
  });
});