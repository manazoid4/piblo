import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TodayPromises } from '../src/components/TodayPromises';

const mockCards = vi.hoisted(() => [
  { id: 'p1', state: 'due' as const, title: 'Call mum', note: 'Call mum by Friday 5pm', when: 'Fri 17 17:00', due_label: 'Friday 17 Jan 2025, 17:00 GMT', overdue: false, actions: ['done', 'later'] },
  { id: 'p2', state: 'waiting' as const, title: 'Write report', note: 'Write report by Monday', when: 'Mon 20 09:00', due_label: 'Monday 20 Jan 2025, 09:00 GMT', overdue: false, actions: ['done', 'later'] },
  { id: 'p3', state: 'done' as const, title: 'Buy milk', note: 'Buy milk today', when: 'Thu 16 10:00', due_label: 'Thursday 16 Jan 2025, 10:00 GMT', overdue: false, actions: ['undo'] },
]);

const mockToday = vi.hoisted(() => ({
  data: { ok: true, cards: mockCards, empty: false, counts: { due: 1, waiting: 1, done: 1 } },
  loading: false,
  error: null,
  reload: vi.fn(),
}));

vi.mock('../src/lib/store', () => ({
  useStore: () => ({
    core: { phase: 'running' },
    toast: vi.fn(),
  }),
  usePoll: () => mockToday,
  useAct: () => vi.fn(),
}));

vi.mock('window', () => ({
  piblo: {
    api: vi.fn(),
  },
}));

describe('TodayPromises', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(mockToday, {
      data: { ok: true, cards: mockCards, empty: false, counts: { due: 1, waiting: 1, done: 1 } },
      loading: false,
      error: null,
    });
  });

  it('renders three cards: due, waiting, done with correct actions', () => {
    const html = renderToStaticMarkup(<TodayPromises />);
    expect(html).toContain('Call mum');
    expect(html).toContain('Write report');
    expect(html).toContain('Buy milk');
    expect(html).toContain('DUE');
    expect(html).toContain('WAITING');
    expect(html).toContain('DONE');
    expect(html).toContain('Done');
    expect(html).toContain('+1h');
    expect(html).toContain('Undo');
  });

  it('shows counts in the header', () => {
    const html = renderToStaticMarkup(<TodayPromises />);
    expect(html).toContain('Due 1');
    expect(html).toContain('Waiting 1');
    expect(html).toContain('Done 1');
  });

  it('shows New promise button when not proposing', () => {
    const html = renderToStaticMarkup(<TodayPromises />);
    expect(html).toContain('New promise');
  });

  it('shows Empty state when no cards', () => {
    Object.assign(mockToday, { data: { ok: true, cards: [], empty: true, counts: { due: 0, waiting: 0, done: 0 } } });
    const html = renderToStaticMarkup(<TodayPromises />);
    expect(html).toContain('No promises today');
  });

  it('shows loading spinner when polling', () => {
    Object.assign(mockToday, { loading: true });
    const html = renderToStaticMarkup(<TodayPromises />);
    expect(html).toContain('spinner');
  });

  it('shows error when polling fails', () => {
    Object.assign(mockToday, { error: 'Failed to load', loading: false });
    const html = renderToStaticMarkup(<TodayPromises />);
    expect(html).toContain('Could not load promises');
    expect(html).toContain('Failed to load');
  });
});