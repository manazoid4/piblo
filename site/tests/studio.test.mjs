import assert from 'node:assert/strict';
import test from 'node:test';
import { createState, press, tick, render, validate, manifest } from '../studio/apps.js';

test('timer counts down with injected time, pauses, stops at 0', () => {
  let s = createState('timer', { minutes: 2 }, 0);
  assert.equal(render(s, 0).big, '02:00');
  s = press(s, 'yes', 1000);
  assert.equal(render(s, 1000 + 30000).big, '01:30');
  s = press(s, 'yes', 31000); // pause with 01:30 left
  assert.equal(render(s, 99999).big, '01:30');
  assert.equal(render(s, 99999).sub, 'Paused');
  s = press(s, 'yes', 100000); // resume
  s = tick(s, 100000 + 90000);
  assert.equal(s.running, false);
  assert.equal(render(s, 999999).big, '00:00');
  assert.equal(s.done, true);
  s = press(s, 'del', 200000);
  assert.equal(render(s, 200000).big, '02:00');
});

test('timer dial changes minutes only while stopped, within 1-90', () => {
  let s = createState('timer', { minutes: 1 }, 0);
  s = press(s, 'down', 0);
  assert.equal(render(s, 0).big, '01:00');
  s = press(s, 'up', 0);
  assert.equal(render(s, 0).big, '02:00');
  s = press(s, 'yes', 0);
  assert.equal(render(press(s, 'up', 1000), 1000).big, '01:59');
});

test('tally floors at 0 and bar caps at 1', () => {
  let s = createState('tally', { goal: 2 }, 0);
  s = press(s, 'del', 0);
  assert.equal(render(s).big, '0');
  for (let i = 0; i < 5; i++) s = press(s, 'yes', 0);
  assert.equal(render(s).big, '5');
  assert.equal(render(s).bar, 1);
  assert.equal(render(press(createState('tally', { goal: 4 }), 'yes')).bar, 0.25);
});

test('list toggles, deletes and clamps cursor', () => {
  let s = createState('list', { items: 'a\nb\nc' }, 0);
  s = press(s, 'up', 0);
  assert.equal(s.cursor, 0);
  s = press(press(s, 'down', 0), 'yes', 0);
  const r = render(s);
  assert.equal(r.big, '2 left');
  assert.deepEqual(r.rows.map((x) => x.done), [false, true, false]);
  s = press(press(s, 'down', 0), 'down', 0);
  assert.equal(s.cursor, 2);
  s = press(s, 'del', 0);
  assert.equal(s.cursor, 1);
  assert.deepEqual(render(s).rows.map((x) => x.text), ['a', 'b']);
  s = press(press(s, 'del', 0), 'del', 0);
  assert.equal(render(press(s, 'del', 0)).big, '0 left');
});

test('validate clamps and sanitises', () => {
  assert.deepEqual(validate('timer', { label: 'a\u0000b\nc', minutes: 500 }), { label: 'abc', minutes: 90 });
  assert.equal(validate('timer', { minutes: 0 }).minutes, 1);
  assert.equal(validate('timer', { minutes: 'x' }).minutes, 25);
  assert.equal(validate('tally', { goal: 99 }).goal, 50);
  const l = validate('list', { items: Array.from({ length: 9 }, (_, i) => `${i}`.repeat(40)).join('\n') + '\n\n' });
  assert.equal(l.items.length, 6);
  assert.ok(l.items.every((i) => i.length === 24));
  assert.throws(() => validate('nope', {}));
});

test('manifest round-trips through JSON', () => {
  const m = manifest('My app', 'list', '#5fd3a5', { title: 'T', items: 'x\ny' });
  assert.deepEqual(m, { name: 'My app', kind: 'list', accent: '#5fd3a5', params: { title: 'T', items: ['x', 'y'] } });
  const back = JSON.parse(JSON.stringify(m));
  assert.deepEqual(manifest(back.name, back.kind, back.accent, back.params), m);
  assert.equal(manifest('', 'tally', '#000', {}).accent, '#ff7a1a');
});
