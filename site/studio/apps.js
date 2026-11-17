// Pure app logic for the Piblo App Studio preview. No DOM; time is always injected (ms).
export const ACCENTS = ['#ff7a1a', '#5fd3a5', '#8b7cf6'];

export const APPS = {
  timer: { title: 'Focus timer', defaults: { label: 'Focus', minutes: 25 } },
  tally: { title: 'Tally', defaults: { label: 'Water', goal: 8 } },
  list: { title: 'Tonight list', defaults: { title: 'Tonight', items: ['Lock up', 'Plan tomorrow', 'Charge phone'] } },
};

const clamp = (n, lo, hi, fallback) => {
  n = Math.round(Number(n));
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback;
};
const clean = (s, max) => String(s ?? '').replace(/[\u0000-\u001f\u007f-\u009f]/g, '').trim().slice(0, max);

export function validate(kind, params = {}) {
  const d = APPS[kind]?.defaults;
  if (!d) throw new Error(`Unknown app: ${kind}`);
  if (kind === 'timer') return { label: clean(params.label ?? d.label, 24), minutes: clamp(params.minutes, 1, 90, d.minutes) };
  if (kind === 'tally') return { label: clean(params.label ?? d.label, 24), goal: clamp(params.goal, 1, 50, d.goal) };
  const raw = Array.isArray(params.items) ? params.items : String(params.items ?? d.items.join('\n')).split(/\r?\n/);
  return { title: clean(params.title ?? d.title, 24), items: raw.map((i) => clean(i, 24)).filter(Boolean).slice(0, 6) };
}

export function createState(kind, params, now = 0) {
  const p = validate(kind, params);
  if (kind === 'timer') return { kind, p, mins: p.minutes, remaining: p.minutes * 60000, running: false, endAt: 0, done: false };
  if (kind === 'tally') return { kind, p, count: 0 };
  return { kind, p, items: p.items.map((text) => ({ text, done: false })), cursor: 0 };
}

const left = (s, now) => (s.running ? Math.max(0, s.endAt - now) : s.remaining);

export function tick(s, now) {
  if (s.kind !== 'timer' || !s.running) return s;
  return s.endAt - now > 0 ? s : { ...s, running: false, remaining: 0, done: true, endAt: 0 };
}

export function press(s, key, now) {
  if (s.kind === 'timer') {
    s = tick(s, now);
    const reset = (mins) => ({ ...s, mins, remaining: mins * 60000, running: false, endAt: 0, done: false });
    if (key === 'yes') {
      if (s.done) return reset(s.mins);
      if (s.running) return { ...s, running: false, remaining: left(s, now), endAt: 0 };
      return { ...s, running: true, endAt: now + s.remaining };
    }
    if (key === 'del') return reset(s.mins);
    if ((key === 'up' || key === 'down') && !s.running) return reset(clamp(s.mins + (key === 'up' ? 1 : -1), 1, 90, s.mins));
    return s;
  }
  if (s.kind === 'tally') {
    if (key === 'yes') return { ...s, count: s.count + 1 };
    if (key === 'del') return { ...s, count: Math.max(0, s.count - 1) };
    return s;
  }
  const n = s.items.length;
  if (key === 'up') return { ...s, cursor: Math.max(0, s.cursor - 1) };
  if (key === 'down') return { ...s, cursor: Math.min(Math.max(0, n - 1), s.cursor + 1) };
  if (key === 'yes' && n) return { ...s, items: s.items.map((it, i) => (i === s.cursor ? { ...it, done: !it.done } : it)) };
  if (key === 'del' && n) {
    const items = s.items.filter((_, i) => i !== s.cursor);
    return { ...s, items, cursor: Math.min(s.cursor, Math.max(0, items.length - 1)) };
  }
  return s;
}

export function render(s, now = 0) {
  if (s.kind === 'timer') {
    const sec = Math.ceil(left(s, now) / 1000);
    const mm = String(Math.floor(sec / 60)).padStart(2, '0');
    const sub = s.done ? 'Done. Press YES to go again' : s.running ? 'Running' : s.remaining < s.mins * 60000 ? 'Paused' : 'Ready';
    return { title: s.p.label || 'Focus', big: `${mm}:${String(sec % 60).padStart(2, '0')}`, sub, rows: [], bar: null };
  }
  if (s.kind === 'tally') {
    return { title: s.p.label || 'Tally', big: String(s.count), sub: `Goal ${s.p.goal}`, rows: [], bar: Math.min(1, s.count / s.p.goal) };
  }
  const open = s.items.filter((i) => !i.done).length;
  return {
    title: s.p.title || 'List',
    big: `${open} left`,
    sub: s.items.length ? '' : 'Nothing on the list',
    rows: s.items.map((it, i) => ({ text: it.text, done: it.done, selected: i === s.cursor })),
    bar: null,
  };
}

export function manifest(name, kind, accent, params) {
  return {
    name: clean(name, 32) || APPS[kind].title,
    kind,
    accent: ACCENTS.includes(accent) ? accent : ACCENTS[0],
    params: validate(kind, params),
  };
}
