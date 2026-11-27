import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement, type ReactElement } from 'react';
import { flowStatusLine, learnedEntries, LearnedBlock, type LearnedEntry } from '../src/components/FlowSection';

const mk = (o: Partial<LearnedEntry>): LearnedEntry => ({ id: 'a', term: 'Piblo', misheard: 'pibble', seen: 5, status: 'active', ...o });

describe('learned automatically', () => {
  it('reads a bare list or {entries} and drops rejected/undone', () => {
    const list = [mk({}), mk({ id: 'b', status: 'undone' }), mk({ id: 'c', status: 'rejected' }), mk({ id: 'd', status: 'candidate' })];
    expect(learnedEntries(list).map((e) => e.id)).toEqual(['a', 'd']);
    expect(learnedEntries({ entries: list }).map((e) => e.id)).toEqual(['a', 'd']);
  });

  it('renders nothing when the endpoint is missing (404 gives no data)', () => {
    expect(learnedEntries(null)).toEqual([]);
    expect(renderToStaticMarkup(createElement(LearnedBlock, { entries: [], onUndo: () => {} }))).toBe('');
  });

  it('renders rules, app name, seen count, candidates and undo buttons', () => {
    const entries = [mk({ app: 'Chrome' }), mk({ id: 'd', status: 'candidate', seen: 1, misheard: 'sonar', term: 'Sonarr' })];
    const html = renderToStaticMarkup(createElement(LearnedBlock, { entries, onUndo: () => {} }));
    expect(html).toContain('Learned automatically');
    expect(html).toContain('pibble →');
    expect(html).toContain('in Chrome');
    expect(html).toContain('seen 5 times');
    expect(html).toContain('needs 1 more');
    expect(html.match(/>Undo</g)?.length).toBe(2);
  });

  it('undo passes the entry id', () => {
    const calls: string[] = [];
    const tree = LearnedBlock({ entries: [mk({})], onUndo: (id) => calls.push(id) }) as ReactElement;
    const find = (n: any): any => (!n || typeof n !== 'object' ? null : n.props?.onClick ? n : [n.props?.children].flat(Infinity).map(find).find(Boolean));
    find(tree).props.onClick();
    expect(calls).toEqual(['a']);
  });
});

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