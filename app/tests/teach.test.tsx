import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { Teach } from '../src/pages/Teach';

vi.mock('../src/lib/store', () => ({
  useAct: () => vi.fn(),
  usePoll: (path: string) => ({ data: path === '/teach' ? { sessions: [
    { session_id: 'teach_abcdefghi', state: 'ready', created_at: 1, transcript: 'Open the project', recording: 'C:\\demo.mp4' },
    { session_id: 'teach_abcdefghj', state: 'recording', created_at: 1, transcript: 'Active', recording: '' },
  ] } : { skills: [] }, loading: false, error: null, reload: vi.fn() }),
}));

it('offers finished recordings for review without offering active recordings', () => {
  const html = renderToStaticMarkup(<Teach />);
  expect(html).toContain('Create draft');
  expect(html).toContain('Open the project');
  expect(html).toContain('demo.mp4');
  expect(html).not.toContain('Active');
  expect(html).toContain('do not run automatically');
});
