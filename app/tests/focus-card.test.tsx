import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FocusCard } from '../src/pages/Actions';

const focus = vi.hoisted(() => ({ state: 'idle', task: '', remaining_s: 0 }));
vi.mock('../src/lib/store', () => ({
  usePoll: () => ({ data: focus, reload: vi.fn() }),
  useAct: () => vi.fn(),
}));

describe('Focus sprint controls', () => {
  beforeEach(() => Object.assign(focus, { state: 'idle', task: '', remaining_s: 0 }));

  it('keeps Finish and Stop available after the timer expires', () => {
    Object.assign(focus, { state: 'awaiting_done', task: 'Write the note' });
    const html = renderToStaticMarkup(<FocusCard />);
    expect(html).toContain('Write the note');
    expect(html).toContain('Time is up. Did you finish?');
    expect(html).toContain('>Finish</button>');
    expect(html).toContain('>Stop</button>');
    expect(html).not.toContain('>25 min</button>');
  });

  it('allows ending a running sprint without replacing it', () => {
    Object.assign(focus, { state: 'running', task: 'Write', remaining_s: 125 });
    const html = renderToStaticMarkup(<FocusCard />);
    expect(html).toContain('2 min left');
    expect(html).toContain('>Finish</button>');
    expect(html).toContain('>Stop</button>');
  });

  it('offers existing presets once the sprint is idle', () => {
    const html = renderToStaticMarkup(<FocusCard />);
    for (const minutes of [15, 25, 50]) expect(html).toContain(`>${minutes} min</button>`);
    expect(html).not.toContain('>Finish</button>');
    expect(html).not.toContain('>Stop</button>');
  });
});
