import { describe, it, expect } from 'vitest';
import { agentTone, groupByAgent, nextAction, type MissionAgent, type MissionTask } from '../src/lib/mission';

const t = (id: string, agent: string, status: MissionTask['status']): MissionTask => ({ id, agent, title: id, status });
const a = (id: string, o: Partial<MissionAgent> = {}): MissionAgent => ({ id, label: id, installed: true, active: false, pct_left: 50, ...o });

describe('mission', () => {
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
