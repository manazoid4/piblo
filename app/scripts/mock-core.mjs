// A small stand-in for Piblo on this PC, so the app can be developed, tested and screenshotted
// without Python or hardware. It answers the same routes the real Core does, including the
// pairing routes from docs/protocol/DEVICE-PAIRING.md and a real PNG for Live View.
// Usage: node scripts/mock-core.mjs [--offline-device] [--no-pairing]   (prints the env the app needs)
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';

const W = 240;
const H = 135;

function crc32(buf) {
  let c = ~0;
  for (const byte of buf) {
    c ^= byte;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

/** Minimal PNG encoder: RGB888, no filter, one zlib stream. Keeps the mock dependency-free. */
export function encodePng(width, height, rgb) {
  const stride = width * 3;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0;
    rgb.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // colour type: truecolour
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 6 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** What the device shows: a dark screen with a tomato header and a few lines, like the real home screen. */
function drawScreen() {
  const rgb = Buffer.alloc(W * H * 3);
  const put = (x, y, r, g, b) => {
    if (x < 0 || y < 0 || x >= W || y >= H) return;
    const o = (y * W + x) * 3;
    rgb[o] = r;
    rgb[o + 1] = g;
    rgb[o + 2] = b;
  };
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) put(x, y, 42, 26, 18);
  for (let y = 0; y < 26; y++) for (let x = 0; x < W; x++) put(x, y, 238, 90, 54);
  const rows = ['Piblo', 'Ask me anything', 'Actions: 2 waiting', 'Memory: 3 saved', 'Wi-Fi: home'];
  rows.forEach((_, i) => {
    const top = 40 + i * 18;
    const len = 60 + ((i * 47) % 120);
    for (let y = top; y < top + 8; y++) for (let x = 14; x < 14 + len; x++) put(x, y, i === 0 ? 255 : 200, i === 0 ? 244 : 190, i === 0 ? 224 : 175);
  });
  const png = encodePng(W, H, rgb);
  const rgb565 = Buffer.alloc(W * H * 2);
  for (let p = 0; p < W * H; p++) {
    const v = ((rgb[p * 3] >> 3) << 11) | ((rgb[p * 3 + 1] >> 2) << 5) | (rgb[p * 3 + 2] >> 3);
    rgb565[p * 2] = v & 255;
    rgb565[p * 2 + 1] = v >> 8;
  }
  return { png, rgb565 };
}

export function startMock({ port = 18787, token = 'mock-token-123', deviceOnline = true, pairing = true } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'piblo-mock-'));
  fs.writeFileSync(path.join(dir, '.env'), `MAZ_TOKEN=${token}\nMAZ_PORT=${port}\nMAZ_DEFAULT_ROUTE=local\nMAZ_GROQ_API_KEY=x\n`);
  const now = () => Math.floor(Date.now() / 1000);
  const frame = drawScreen();
  const flow = { settings: { input_mode: 'paste', press_enter: false, cleanup: 'auto', stt: 'auto', effective_cleanup: 'rules', has_groq_key: false }, lines: ['Piblo', 'Cardputer', 'Piblo Flow', 'Groq', 'Rampage: ram page, ram payge'], get words() { return this.lines.map((l) => l.split(':')[0].trim()); } };
  const db = {
    beam: [{ id: 'b1', text: 'https://example.com/read-later', direction: 'to_pocket', created: now() - 4000, kind: 'link' }],
    dumps: [
      { id: 'd1', summary: 'Call the printer about the invoice before Friday', status: 'new', project: 'admin', created: new Date().toISOString(), source: 'device' },
      { id: 'd2', summary: 'Idea: a weekly digest of everything saved from Piblo', status: 'claimed', claimed_by: 'agent', project: 'piblo', created: new Date().toISOString(), source: 'flow' },
      { id: 'd3', summary: 'Book dentist', status: 'done', project: 'personal', created: new Date().toISOString(), source: 'device' },
    ],
    pending: [{ id: 'p1', tool: 'Bash', summary: 'git push origin feat/piblo-windows-app', project: 'piblo-app', left: 84 }],
    needs: [
      { id: 'pr-1', tier: 'prs', title: 'Piblo Windows app, Stage 2', project: 'piblo-app', age: 7200, detail: '' },
      { id: 'act-1', tier: 'actions', title: 'Approve the launch copy', project: 'handoff', age: 90000, detail: '' },
    ],
    halted: false,
    focus: { state: 'idle', muted: false, remaining_s: 0 },
    session: 0,
    // Pairing requests, exactly as Core lists them (120 s expiry, oldest first).
    pair: pairing ? [{ request_id: 'req-482913-opaque-id-01', code: '482913', name: 'Piblo', device_id: 'piblo-a1b2c3', ip: '192.168.1.42', created: now() - 8 }] : [],
    devLists: [
      {
        id: 'founder-today',
        items: [
          { id: 'pair-device', text: 'Pair Piblo with the Windows app and check the Connected pill', done: true, when: new Date().toISOString(), note: 'Verified on desk' },
          { id: 'film-demo', text: 'Record a 30-second clip of Ask on Piblo answering a question', done: false },
          { id: 'review-draft', text: 'Approve one launch update in Content Studio', done: false },
        ],
      },
      {
        id: 'feature-tests',
        items: [
          { id: 'ask-on-piblo', text: 'Ask on Piblo answers a question and speaks the reply', done: true, when: new Date().toISOString(), note: 'Clear voice' },
          { id: 'private-mode', text: 'Private mode blocks network tools until turned off', done: false },
          { id: 'live-view', text: 'Live View mirrors the screen and sends keys cleanly', done: false },
        ],
      },
      {
        id: 'kickstarter-gates',
        items: [
          { id: 'hero-video', text: 'Hero video shows the real Piblo unit working end to end', done: false },
          { id: 'reward-tiers', text: 'Reward tiers and UK shipping costs double-checked', done: false },
          { id: 'page-preview', text: 'Campaign page preview shared with early testers', done: false },
        ],
      },
    ],
    devDrafts: [
      {
        id: 'draft-1',
        title: 'Ask on Piblo: calm voice answers on your desk',
        body: 'Ask on Piblo: calm voice answers on your desk.\n\nPress Ask, speak a quick question, and Piblo answers in plain words while keeping your notes on your own PC.',
        media_path: null,
        link: 'https://piblo.co.uk',
        targets: [
          {
            platform: 'reddit',
            subreddit: 'SideProject',
            post_type: 'text',
            title: 'I built Ask on Piblo — a small desk companion that answers in plain words',
            body: 'Ask on Piblo: calm voice answers on your desk.\n\nHappy to answer questions about the hardware or Windows companion app.',
          },
          {
            platform: 'reddit',
            subreddit: 'LocalLLaMA',
            post_type: 'text',
            title: 'Piblo: running a local voice and note companion on a home PC',
            body: 'Runs against a local model on your PC with a pocket screen over Wi-Fi.',
          },
          {
            platform: 'x',
            title: null,
            body: 'Ask on Piblo: press one key on your desk, ask a question, and get a calm spoken answer from your own PC. https://piblo.co.uk',
          },
          {
            platform: 'linkedin',
            title: 'Ask on Piblo',
            body: 'Building Piblo: a calm pocket companion that pairs with your Windows PC.\n\nToday we finished Ask on Piblo with local voice replies.',
          },
          {
            platform: 'instagram',
            title: null,
            body: 'Ask on Piblo: calm voice answers on your desk.\n\n#piblo #buildinpublic #hardware #desksetup',
          },
        ],
        status: 'draft',
        created: new Date().toISOString(),
        approved_at: null,
        posted_at: null,
      },
    ],
  };
  const live = () => db.pair.filter((r) => now() - r.created < 120);
  const json = (res, code, body) => {
    res.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' });
    res.end(JSON.stringify(body));
  };

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x');
    const p = url.pathname;
    let body = '';
    for await (const c of req) body += c;
    const data = body ? JSON.parse(body) : {};

    // Public: a device asking to connect (no bearer).
    if (p === '/pair/device/request' && req.method === 'POST') {
      const record = { request_id: `req-${Math.random().toString(36).slice(2, 12)}-${now()}`, code: String(100000 + Math.floor(Math.random() * 899999)), name: String(data.name ?? 'Piblo').slice(0, 24), device_id: String(data.device_id ?? 'device'), ip: '192.168.1.42', created: now() };
      db.pair = db.pair.filter((r) => r.device_id !== record.device_id);
      db.pair.push(record);
      return json(res, 200, { request_id: record.request_id, code: record.code, expires_in_seconds: 120 });
    }

    if (p === '/health' && req.headers.authorization !== `Bearer ${token}`) return json(res, 200, { ok: true, name: 'Piblo Core', version: '1.0.2' });
    if (req.headers.authorization !== `Bearer ${token}`) return json(res, 401, { detail: 'authentication_required' });
    if (p === '/health') return json(res, 200, { ok: true, name: 'Piblo Core', version: '1.0.2', llm: { local: true, local_model: 'qwen3.5:4b', local_model_installed: true, local_engine: 'ollama' }, fw_latest: { version: '1.0.2' } });

    // Pairing (bearer): list, approve, deny.
    if (p === '/pair/device/pending') {
      return json(res, 200, { requests: live().sort((a, b) => a.created - b.created).map(({ created, ...r }) => ({ ...r, age_s: now() - created })) });
    }
    const decided = p.match(/^\/pair\/device\/([^/]+)\/(approve|deny)$/);
    if (decided) {
      const [, id, action] = decided;
      const record = live().find((r) => r.request_id === decodeURIComponent(id));
      if (!record) return json(res, 404, { detail: 'not_found' });
      db.pair = db.pair.filter((r) => r.request_id !== record.request_id);
      return json(res, 200, { ok: true, decision: action });
    }

    if (p === '/core/device') return json(res, 200, deviceOnline
      ? { device: { ok: true, version: '1.0.2', fw_build: 'a1b2c3d4e5f6', battery: 82, app: 'HOME' }, online: true, hub_fw: { version: '1.0.3', sha: 'ffeeddccbbaa', size: 1200000 }, update_available: true, last_report: { stage: 'ok' } }
      : { device: { ok: false, error: 'cardputer_unreachable' }, online: false, hub_fw: null, update_available: false });

    const offline = () => json(res, 503, { detail: 'cardputer_unreachable' });
    if (p === '/core/cardputer/screen') {
      if (!deviceOnline) return offline();
      res.writeHead(200, { 'content-type': 'application/octet-stream', 'x-maz-format': 'RGB565LE', 'cache-control': 'no-store' });
      return res.end(frame.rgb565);
    }
    if (p === '/core/cardputer/screen.png') {
      if (!deviceOnline) return offline();
      res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store' });
      return res.end(frame.png);
    }
    if (p === '/core/cardputer/key') {
      if (!deviceOnline) return offline();
      if (typeof data.keys !== 'string' || data.keys.length < 1 || data.keys.length > 400) return json(res, 400, { detail: 'bad_keys' });
      return json(res, 200, { ok: true, typed: data.keys.length });
    }
    if (p === '/core/cardputer/action') {
      if (!deviceOnline) return offline();
      return json(res, 200, { ok: true, status: 200, text: 'ok' });
    }
    if (p === '/core/update/check') return json(res, 200, { ok: true });
    if (p === '/beam/history') return json(res, 200, { ok: true, messages: [...db.beam].reverse() });
    if (p === '/beam/to-pocket') {
      const m = { id: 'b' + (db.beam.length + 1), text: data.text, direction: 'to_pocket', created: now(), kind: 'text' };
      db.beam.push(m);
      return json(res, 200, { ok: true, queued: true, message: m });
    }
    if (p === '/needs' && req.method === 'GET') return json(res, 200, { count: db.needs.length, items: db.needs });
    if (p.startsWith('/needs/')) {
      db.needs = db.needs.filter((n) => n.id !== p.split('/')[2]);
      return json(res, 200, { ok: true });
    }
    if (p === '/buddy/pending') return json(res, 200, { ok: true, pending: db.pending });
    if (p === '/buddy/decision' || p === '/buddy/decide') {
      db.pending = [];
      return json(res, 200, { ok: true });
    }
    if (p === '/buddy/summary') return json(res, 200, { ok: true, today: { done: 7, needs: 2 } });
    if (p === '/buddy/today') return json(res, 200, { ok: true, done: 7, needs: 2, errors: 0, approved: 3, denied: 1, agents: [{ source: 'claude', done: 5 }], last: { text: 'Finished the desktop app build' } });
    if (p === '/buddy/halt') {
      if (req.method === 'POST') db.halted = !!data.on;
      return json(res, 200, { ok: true, halted: db.halted, until: db.halted ? new Date(Date.now() + 900000).toISOString() : null });
    }
    if (p === '/work/today') return json(res, 200, { items: [{ item_id: 'w1', action: 'Send the follow-up to the first client', reason: 'Stored follow-up or deadline is due now.' }] });
    if (p === '/focus/state') return json(res, 200, db.focus);
    if (p === '/focus/start') {
      db.focus = { state: 'running', task: data.task, remaining_s: data.minutes * 60, muted: true };
      return json(res, 200, db.focus);
    }
    if (p === '/focus/end') {
      db.focus = { state: 'idle', muted: false, remaining_s: 0 };
      return json(res, 200, db.focus);
    }
    if (p === '/pc/action') return json(res, 200, { ok: true, reply: 'Done' });
    if (p === '/dumps' && req.method === 'GET') return json(res, 200, { dumps: url.searchParams.get('status') ? db.dumps.filter((d) => d.status === url.searchParams.get('status')) : db.dumps });
    if (p === '/dumps') {
      db.dumps.unshift({ id: 'd' + (db.dumps.length + 1), summary: data.text.slice(0, 280), transcript: data.text, status: 'new', project: null, created: new Date().toISOString(), source: 'web' });
      return json(res, 200, { dump_id: 'x' });
    }
    if (/^\/dumps\/[^/]+$/.test(p) && req.method === 'GET') {
      const d = db.dumps.find((x) => x.id === p.split('/')[2]);
      return d ? json(res, 200, { ...d, transcript: d.transcript ?? d.summary }) : json(res, 404, { detail: 'dump_not_found' });
    }
    if (p.endsWith('/done')) {
      const d = db.dumps.find((x) => x.id === p.split('/')[2]);
      if (d) d.status = 'done';
      return json(res, 200, { ok: true });
    }
    if (p === '/library/search') return json(res, 200, { results: [{ id: 'l1', kind: 'web', url: 'https://example.com/a', author: 'example', snippet: 'A [saved] article about calm software', project: null }] });
    if (p === '/models') return json(res, 200, { local: true, local_engine: 'ollama', local_model: 'qwen3.5:4b', local_model_installed: true, backup_model: 'lfm2.5-8b', default_route: 'local', last_route: { provider: 'ollama' } });
    // Flow dictation settings (/flow/*)
    if (p === '/flow/settings') {
      if (req.method === 'GET' || req.method === 'PATCH' || req.method === 'POST') {
        if (req.method !== 'GET' && data) Object.assign(flow.settings, data);
        flow.settings.effective_cleanup = flow.settings.cleanup === 'auto' ? 'rules' : flow.settings.cleanup;
        flow.settings.has_groq_key = false;
      }
      return json(res, 200, flow.settings);
    }
    if (p === '/flow/dictionary' || p.startsWith('/flow/dictionary/')) {
      const dictPart = (line) => { const [term, rest] = String(line).split(':'); return { term: term.trim(), aliases: (rest || '').split(',').map((a) => a.trim()).filter(Boolean) }; };
      const dictView = () => { const entries = flow.lines.map(dictPart); return { words: entries.map((e) => e.term), entries, aliased: entries.filter((e) => e.aliases.length).length }; };
      if (req.method === 'GET') return json(res, 200, dictView());
      if (req.method === 'POST' || req.method === 'PATCH') {
        const w = (data?.word || '').trim();
        if (!w) return json(res, 400, { detail: 'word_required' });
        const { term, aliases } = dictPart(w);
        if (!term) return json(res, 400, { detail: 'word_required' });
        const index = flow.lines.findIndex((l) => dictPart(l).term.toLowerCase() === term.toLowerCase());
        if (index < 0) flow.lines.push(term + (aliases.length ? ': ' + aliases.join(', ') : ''));
        else {
          const known = dictPart(flow.lines[index]).aliases;
          const merged = [...known, ...aliases.filter((a) => !known.some((k) => k.toLowerCase() === a.toLowerCase()))];
          if (merged.length !== known.length) flow.lines[index] = term + ': ' + merged.join(', ');
        }
        return json(res, 200, dictView());
      }
      if (req.method === 'DELETE') {
        const w = decodeURIComponent(p.split('/')[3] || '').toLowerCase();
        if (!w) return json(res, 400, { detail: 'word_required' });
        flow.lines = flow.lines.filter((l) => dictPart(l).term.toLowerCase() !== w);
        return json(res, 200, dictView());
      }
    }
    if (p === '/flow/status') return json(res, 200, { recent: [{ at: new Date().toISOString(), chars: 84, intent: 'paste', cleanup: 'rules', total_ms: 640 }], count: 1, avg_total_ms: 640, p95_total_ms: 640, errors: [], effective_cleanup: flow.settings.effective_cleanup });
    if (p === '/flow/test') return json(res, 200, { ms: 420, word_count: 5, cleanup: flow.settings.cleanup, effective_cleanup: flow.settings.effective_cleanup, text: 'This is a test of dictation.' });
    if (p === '/voices') return json(res, 200, { ok: true, current: 'v1', current_name: 'Adrian', voices: [{ name: 'Adrian', reference_id: 'v1', description: 'Warm, steady', current: true }, { name: 'Maya', reference_id: 'v2', description: 'Bright and quick' }, { name: 'Theo', reference_id: 'v3', description: 'Low and calm' }] });
    if (p === '/session/start') return json(res, 200, { session_id: 's' + ++db.session });
    if (p === '/turn/text') return json(res, 200, { reply: 'Start with the follow-up to your first client, then clear the two items waiting on you.', provider: 'ollama' });
    if (p === '/pair/start') return json(res, 200, { code: 'K7Q2-M9XD', expires_in_seconds: 300 });
    if (p === '/diagnostics') return json(res, 200, { ok: true, cardputer: { connected: deviceOnline } });

    // Piblo Dev routes (/dev/*)
    if (p === '/features') return json(res, 200, { features: [{ id: 'maker', name: 'Maker', summary: 'Printer status and safe actions.', category: 'maker', source: 'host/mazhost/maker.py', dependencies: 'Supported printer connection', status: 'simulated', steps: ['Open Maker on Piblo', 'Check printer status'], evidence: { who: null, when: null, note: '' } }, { id: 'private-mode', name: 'Private mode', summary: 'Keep work on your own computers.', category: 'secure', source: 'host/mazhost/private_mode.py', dependencies: 'None', status: 'simulated', steps: ['Enable private mode', 'Check a cloud action is blocked'], evidence: { who: null, when: null, note: '' } }] });
    if (p === '/dev/workflow-templates') return json(res, 200, { templates: [{ id: 'audit-prs', title: 'Audit PRs', agent_kind: 'codex', steps: ['List open pull requests and their checks.', 'Review the changes and report actionable findings.'], checks: ['Relevant tests pass', 'Review findings are resolved'] }, { id: 'fix-ci', title: 'Fix CI', agent_kind: 'codex', steps: ['Inspect the failing check.', 'Reproduce and fix the cause.'], checks: ['The failing check passes'] }] });
    if (p === '/dev/memory-settings') return json(res, 200, { memory_repo_path: '' });
    if (p === '/dev/task-drafts' && req.method === 'GET') return json(res, 200, { drafts: [] });
    if (p === '/dev/memory') return json(res, 200, { configured: true, headings: ['# Memory index', '## Projects'], handoff: '# Latest handover\nReview the next Piblo task.', updated: '2026-11-12T12:00:00Z', results: [] });
    if (p === '/agents/providers') return json(res, 200, { providers: { claude: { available: true }, codex: { available: true }, hermes: { available: false } } });
    if (p === '/agents/jobs') return json(res, 200, { jobs: [{ job_id: 'example', provider: 'codex', task: 'Review the latest changes', state: 'done' }] });
    if (p === '/core/projects') return json(res, 200, { projects: [{ name: 'example' }] });
    if (p === '/authority/request' && req.method === 'POST') { db.agentRequest = data; return json(res, 200, { request_id: 'mock-request', status: 'pending' }); }
    if (p === '/authority/request/mock-request') return json(res, 200, db.agentApproved ? { status: 'approved', grant_token: 'mock-approved-grant' } : { status: 'pending' });
    if (p === '/agents/run') { db.agentRuns = (db.agentRuns || 0) + 1; db.agentRun = data; return json(res, db.agentApproved ? 200 : 403, { ok: !!db.agentApproved }); }
    if (p === '/mock/approve' && req.method === 'POST') { db.agentApproved = true; return json(res, 200, { ok: true }); }
    if (p === '/mock/agent-runs') return json(res, 200, { count: db.agentRuns || 0, request: db.agentRequest, run: db.agentRun });
    if (p === '/dev/checklists' && req.method === 'GET') {
      return json(res, 200, { lists: db.devLists });
    }
    const chkMatch = p.match(/^\/dev\/checklists\/([^/]+)\/items\/([^/]+)$/);
    if (chkMatch && req.method === 'POST') {
      const [, listId, itemId] = chkMatch.map(decodeURIComponent);
      const list = db.devLists.find((l) => l.id === listId);
      const item = list?.items.find((i) => i.id === itemId);
      if (!item) return json(res, 404, { detail: 'checklist_item_not_found' });
      item.done = data.done !== false;
      item.when = new Date().toISOString();
      item.note = data.note ?? null;
      return json(res, 200, item);
    }
    if (p === '/dev/content' && req.method === 'GET') {
      const status = url.searchParams.get('status');
      const drafts = status ? db.devDrafts.filter((d) => d.status === status) : db.devDrafts;
      return json(res, 200, { drafts });
    }
    if (p === '/dev/content/from-feature' && req.method === 'POST') {
      const feature = String(data.feature ?? '').trim();
      const summary = String(data.summary ?? '').trim();
      if (!feature || !summary) return json(res, 400, { detail: 'feature and summary required' });
      const base = `${feature}: ${summary}`;
      const draft = {
        id: 'draft-' + (db.devDrafts.length + 1),
        title: `${feature} working on Piblo`,
        body: base,
        media_path: data.clip_path ?? null,
        link: 'https://piblo.co.uk',
        targets: [
          { platform: 'reddit', subreddit: 'SideProject', post_type: 'text', title: `${feature} on Piblo`, body: base },
          { platform: 'x', title: null, body: base.slice(0, 280) },
          { platform: 'linkedin', title: feature, body: base },
          { platform: 'instagram', title: null, body: `${base}\n\n#piblo #buildinpublic` },
        ],
        status: 'draft',
        created: new Date().toISOString(),
        approved_at: null,
        posted_at: null,
      };
      db.devDrafts.unshift(draft);
      return json(res, 200, draft);
    }
    const approveMatch = p.match(/^\/dev\/content\/([^/]+)\/approve$/);
    if (approveMatch && req.method === 'POST') {
      const id = decodeURIComponent(approveMatch[1]);
      const draft = db.devDrafts.find((d) => d.id === id);
      if (!draft) return json(res, 404, { detail: 'draft_not_found' });
      draft.status = 'approved';
      draft.approved_at = new Date().toISOString();
      const shares = [
        { platform: 'reddit', subreddit: 'SideProject', url: 'https://www.reddit.com/r/SideProject/submit?title=' + encodeURIComponent(draft.title) },
        { platform: 'x', url: 'https://x.com/intent/tweet?text=' + encodeURIComponent(draft.body.slice(0, 280)) },
        { platform: 'linkedin', url: 'https://www.linkedin.com/feed/?shareActive=true&text=' + encodeURIComponent(draft.body) },
        { platform: 'instagram', caption: draft.body, media_path: draft.media_path },
      ];
      return json(res, 200, { draft, shares });
    }
    const postedMatch = p.match(/^\/dev\/content\/([^/]+)\/posted$/);
    if (postedMatch && req.method === 'POST') {
      const id = decodeURIComponent(postedMatch[1]);
      const draft = db.devDrafts.find((d) => d.id === id);
      if (!draft) return json(res, 404, { detail: 'draft_not_found' });
      draft.status = 'posted';
      draft.posted_at = new Date().toISOString();
      return json(res, 200, draft);
    }
    if (p === '/dev/usage' && req.method === 'GET') {
      return json(res, 200, {
        claude: {
          available: true,
          accounts: [
            {
              number: 1,
              five_hour: { used_percent: 42, resets_at: '18:00' },
              seven_day: { used_percent: 58, resets_at: 'Mon 09:00' },
            },
          ],
        },
        codex: {
          available: true,
          primary: { used_percent: 25, resets_at: '19:30' },
          secondary: { used_percent: 84, resets_at: 'Thu 12:00' },
        },
        opencode: { available: false },
        hermes: { available: true },
      });
    }
    if (p === '/dev/github' && req.method === 'GET') {
      return json(res, 200, {
        available: true,
        prs: [
          {
            number: 95,
            title: 'Piblo Dev section in the Windows app',
            headRefName: 'feat/piblo-dev-hermes',
            url: 'https://github.com/manazoid4/piblo/pull/95',
            statusCheckRollup: [
              { name: 'typecheck', state: 'SUCCESS', conclusion: 'SUCCESS' },
              { name: 'vitest', state: 'SUCCESS', conclusion: 'SUCCESS' },
              { name: 'lint', conclusion: 'FAILURE', detailsUrl: 'https://github.com/manazoid4/piblo/actions' },
            ],
          },
        ],
      });
    }
    if (p === '/dev/overview' && req.method === 'GET') {
      const founder = db.devLists.find((l) => l.id === 'founder-today');
      const founderUnticked = (founder?.items ?? []).filter((i) => !i.done).length;
      const draftsWaiting = db.devDrafts.filter((d) => d.status === 'draft').length;
      return json(res, 200, {
        founder_unticked: founderUnticked,
        failing_pr_checks: 1,
        drafts_awaiting_approval: draftsWaiting,
        usage_warnings: 1,
      });
    }

    json(res, 404, { detail: 'not_found' });
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve({ dir, port, token, close: () => server.close() })));
}

if (process.argv[1] && process.argv[1].endsWith('mock-core.mjs')) {
  const m = await startMock({ deviceOnline: !process.argv.includes('--offline-device'), pairing: !process.argv.includes('--no-pairing') });
  console.log(`mock Piblo on ${m.port}; run the app with:\nNOD_CORE_DIR=${m.dir} NOD_CORE_PORT=${m.port}`);
}
