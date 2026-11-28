import { describe, it, expect } from 'vitest';
import { agentTone, groupByAgent, missionProof, nextAction, type MissionAgent, type MissionTask } from '../src/lib/mission';

const t = (id: string, agent: string, status: MissionTask['status']): MissionTask => ({ id, agent, title: id, status });
const a = (id: string, o: Partial<MissionAgent> = {}): MissionAgent => ({ id, label: id, installed: true, active: false, pct_left: 50, ...o });

describe('mission', () => {
  it('only makes HTTPS proof clickable and keeps relative proof as text', () => {
    expect(missionProof('https://github.com/manazoid4/piblo/pull/208')).toEqual({
      text: 'https://github.com/manazoid4/piblo/pull/208',
      href: 'https://github.com/manazoid4/piblo/pull/208',
    });
    expect(missionProof('screenshots/01-real.png')).toEqual({ text: 'screenshots/01-real.png' });
    for (const value of [null, undefined, '', '   ', 'http://host/proof', 'https://', 'https://user@host/proof',
      'javascript:alert(1)', 'file:///etc/passwd', '//host/proof', 'C:\\proof.png', '../proof.png', 'x'.repeat(301)]) {
      expect(missionProof(value)).toBeNull();
    }
  });

  it('nextAction: failed beats waiting beats running; else all clear', () => {
    expect(nextAction([t('a', 'x', 'running'), t('b', 'x', 'waiting'), t('c', 'x', 'failed')])).toBe('Fix: c');
    expect(nextAction([t('a', 'x', 'running'), t('b', 'x', 'waiting')])).toBe('Unblock: b');
    expect(nextAction([t('a', 'x', 'running'), t('d', 'x', 'done')])).toBe('Watching: a');
    expect(nextAction([t('d', 'x', 'done')])).toBe('All clear');
    expect(nextAction([])).toBe('All clear');
  });
  it('groupByAgent keeps agent order and adds unknown agents', () => {
    const g = groupByAgent([a('c1'), a('c2')], [t('1', 'c2', 'done'), t('2', 'zz', 'done'), t('3', 'c1', 'done')]);
    expect(g.map((x) => [x.id, x.tasks.map((y) => y.id)])).toEqual([['c1', ['3']], ['c2', ['1']], ['zz', ['2']]]);
  });
  it('agentTone: off, warn under 15%, ok when active, else idle', () => {
    expect(agentTone(a('x', { installed: false }))).toBe('off');
    expect(agentTone(a('x', { pct_left: 11, active: true }))).toBe('warn');
    expect(agentTone(a('x', { active: true }))).toBe('ok');
    expect(agentTone(a('x', { pct_left: null }))).toBe('idle');
  });
});
