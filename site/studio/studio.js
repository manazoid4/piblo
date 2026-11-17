// App Studio preview: mounts into every [data-studio]. DOM built with createElement + textContent only.
import { APPS, ACCENTS, createState, press, tick, render, manifest } from './apps.js';

const ACCENT_NAMES = ['Orange', 'Mint', 'Violet'];
const FIELDS = {
  timer: [
    { key: 'label', label: 'Label', type: 'text', max: 24 },
    { key: 'minutes', label: 'Minutes (1 to 90)', type: 'number', min: 1, max: 90 },
  ],
  tally: [
    { key: 'label', label: 'Label', type: 'text', max: 24 },
    { key: 'goal', label: 'Goal (1 to 50)', type: 'number', min: 1, max: 50 },
  ],
  list: [
    { key: 'title', label: 'Title', type: 'text', max: 24 },
    { key: 'items', label: 'Items (one per line, up to 6)', type: 'textarea' },
  ],
};
const KEYS = { Enter: 'yes', Backspace: 'del', Escape: 'back', ArrowUp: 'up', ArrowDown: 'down' };

let uid = 0;
const h = (tag, props = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'text') el.textContent = v;
    else if (k === 'class') el.className = v;
    else if (v === true) el.setAttribute(k, '');
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  el.append(...kids.filter(Boolean));
  return el;
};

function mount(root) {
  const id = `studio${++uid}`;
  const cfg = { kind: 'timer', accent: ACCENTS[0], names: {}, params: {} };
  for (const [k, app] of Object.entries(APPS)) {
    cfg.names[k] = app.title;
    cfg.params[k] = { ...app.defaults };
    if (k === 'list') cfg.params[k].items = app.defaults.items.join('\n');
  }
  let state;
  let timerId = 0;

  // Tabs
  const tablist = h('div', { class: 'studio-tabs', role: 'tablist', 'aria-label': 'Choose a mini-app' });
  const tabs = Object.entries(APPS).map(([kind, app]) =>
    h('button', { type: 'button', role: 'tab', id: `${id}-tab-${kind}`, 'aria-controls': `${id}-panel`, text: app.title, 'data-kind': kind }));
  tablist.append(...tabs);

  // Device
  const buddy = h('div', { class: 'studio-buddy' });
  const screen = h('div', {
    class: 'studio-screen', tabindex: '0', role: 'group',
    'aria-label': 'Piblo Home screen preview. Enter yes, Backspace undo, Escape back, arrows move',
  });
  const key = (cls, label, aria, glyph) => h('button', { type: 'button', class: `studio-key ${cls}`, 'aria-label': aria, 'data-key': label }, h('span', { 'aria-hidden': 'true', text: glyph }), h('small', { 'aria-hidden': 'true', text: cls === 'k-back' ? 'BACK' : cls === 'k-yes' ? 'YES' : 'DEL' }));
  const dial = h('div', { class: 'studio-dial', role: 'group', 'aria-label': 'Dial' },
    h('button', { type: 'button', class: 'studio-key k-dial', 'aria-label': 'Dial up', 'data-key': 'up', text: '▲' }),
    h('button', { type: 'button', class: 'studio-key k-dial', 'aria-label': 'Dial down', 'data-key': 'down', text: '▼' }));
  const keys = h('div', { class: 'studio-keys' },
    key('k-back', 'back', 'Back (Escape)', '‹'),
    key('k-del', 'del', 'Delete or undo (Backspace)', '⌫'),
    dial,
    key('k-yes', 'yes', 'Yes (Enter)', '✓'));
  const device = h('div', { class: 'studio-device' }, buddy, screen, keys);

  // Controls
  const nameInput = h('input', { type: 'text', id: `${id}-name`, maxlength: '32', autocomplete: 'off' });
  const swatches = h('div', { class: 'studio-swatches', role: 'radiogroup', 'aria-label': 'Accent colour' },
    ...ACCENTS.map((c, i) => {
      const r = h('input', { type: 'radio', name: `${id}-accent`, value: c, 'aria-label': ACCENT_NAMES[i] });
      const sw = h('label', { class: 'studio-swatch' }, r, h('span', { 'aria-hidden': 'true' }));
      sw.querySelector('span').style.setProperty('--sw', c);
      return sw;
    }));
  const fields = h('div', { class: 'studio-fields' });
  const status = h('p', { class: 'studio-status', role: 'status', 'aria-live': 'polite' });
  const fallback = h('textarea', { class: 'studio-fallback', readonly: true, rows: '6', 'aria-label': 'App file text', hidden: true });
  const copy = h('button', { type: 'button', class: 'studio-copy', text: 'Copy app file' });
  const controls = h('div', { class: 'studio-controls' },
    h('div', { class: 'studio-field' }, h('label', { for: `${id}-name`, text: 'App name' }), nameInput),
    h('div', { class: 'studio-field' }, h('span', { class: 'studio-label', text: 'Accent' }), swatches),
    fields,
    h('div', { class: 'studio-copyrow' }, copy, status),
    fallback);

  const panel = h('div', { class: 'studio-panel', role: 'tabpanel', id: `${id}-panel` }, device, controls);
  const note = h('p', { class: 'studio-note', text: 'Runs here in your browser. Installing apps on Piblo is planned, not available yet.' });
  root.replaceChildren(tablist, panel, note);

  const params = () => cfg.params[cfg.kind];
  const now = () => Date.now();

  function draw() {
    const r = render(state, now());
    buddy.textContent = `Piblo Buddy · ${cfg.names[cfg.kind] || APPS[cfg.kind].title}`;
    const body = [h('div', { class: 'studio-title', text: r.title }), h('div', { class: 'studio-big', text: r.big })];
    if (r.sub) body.push(h('div', { class: 'studio-sub', text: r.sub }));
    if (r.bar != null) {
      const fill = h('span', { class: 'studio-bar-fill' });
      fill.style.setProperty('--fill', String(r.bar));
      body.push(h('div', { class: 'studio-bar' }, fill));
    }
    if (r.rows.length) {
      body.push(h('ul', { class: 'studio-rows' }, ...r.rows.map((row) =>
        h('li', { class: `${row.selected ? 'is-sel ' : ''}${row.done ? 'is-done' : ''}`.trim() },
          h('span', { class: 'studio-box', 'aria-hidden': 'true', text: row.done ? '✓' : '' }),
          h('span', { text: row.text }),
          row.done ? h('span', { class: 'studio-vh', text: ' (done)' }) : null))));
    }
    screen.replaceChildren(...body);
    // One interval, only while a timer runs.
    if (state.running && !timerId) timerId = setInterval(() => { state = tick(state, now()); draw(); }, 250);
    if (!state.running && timerId) { clearInterval(timerId); timerId = 0; }
  }

  const reset = () => { state = createState(cfg.kind, params(), now()); draw(); };
  const act = (k) => { if (k === 'back') return screen.blur(); state = press(state, k, now()); draw(); };

  function buildFields() {
    fields.replaceChildren(...FIELDS[cfg.kind].map((f, i) => {
      const fid = `${id}-f${i}`;
      const input = h(f.type === 'textarea' ? 'textarea' : 'input', { id: fid, autocomplete: 'off' });
      if (f.type === 'textarea') input.rows = 5;
      else input.type = f.type;
      if (f.type === 'number') { input.min = f.min; input.max = f.max; input.inputMode = 'numeric'; }
      if (f.max && f.type === 'text') input.maxLength = f.max;
      input.value = params()[f.key];
      input.addEventListener('input', () => { params()[f.key] = input.value; reset(); });
      return h('div', { class: 'studio-field' }, h('label', { for: fid, text: f.label }), input);
    }));
  }

  function select(kind, focus) {
    cfg.kind = kind;
    tabs.forEach((t) => {
      const on = t.dataset.kind === kind;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      if (on) { panel.setAttribute('aria-labelledby', t.id); if (focus) t.focus(); }
    });
    nameInput.value = cfg.names[kind];
    buildFields();
    fallback.hidden = true;
    status.textContent = '';
    reset();
  }

  tabs.forEach((t, i) => {
    t.addEventListener('click', () => select(t.dataset.kind));
    t.addEventListener('keydown', (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      const to = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : d ? (i + d + tabs.length) % tabs.length : -1;
      if (to < 0) return;
      e.preventDefault();
      select(tabs[to].dataset.kind, true);
    });
  });
  keys.addEventListener('click', (e) => { const b = e.target.closest('[data-key]'); if (b) act(b.dataset.key); });
  screen.addEventListener('keydown', (e) => {
    const k = KEYS[e.key];
    if (!k || e.altKey || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    act(k);
  });
  nameInput.addEventListener('input', () => { cfg.names[cfg.kind] = nameInput.value; draw(); });
  swatches.addEventListener('change', (e) => { cfg.accent = e.target.value; device.style.setProperty('--accent', cfg.accent); });
  swatches.querySelector('input').checked = true;
  device.style.setProperty('--accent', cfg.accent);

  let statusTimer = 0;
  copy.addEventListener('click', async () => {
    const text = JSON.stringify(manifest(cfg.names[cfg.kind], cfg.kind, cfg.accent, params()), null, 2);
    clearTimeout(statusTimer);
    try {
      await navigator.clipboard.writeText(text);
      fallback.hidden = true;
      status.textContent = 'Copied';
      statusTimer = setTimeout(() => { status.textContent = ''; }, 2500);
    } catch {
      fallback.value = text;
      fallback.hidden = false;
      fallback.select();
      status.textContent = 'Copy blocked. The file text is selected, press Ctrl+C.';
    }
  });

  select('timer');
}

document.querySelectorAll('[data-studio]').forEach(mount);
