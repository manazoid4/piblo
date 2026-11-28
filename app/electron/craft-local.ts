import type { CraftDraft, CraftTaskRecord } from '../shared/types';

export const emptyTask: CraftTaskRecord = { prompt: '', model: '', last: null };
const modelName = /^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}$/;

function checkDraft(draft: CraftDraft) {
  if (!draft || typeof draft.prompt !== 'string' || draft.prompt.length > 12000 ||
      typeof draft.model !== 'string' || (draft.model && !modelName.test(draft.model))) {
    throw new Error('Invalid Craft draft.');
  }
}

export function parseTask(text: string): CraftTaskRecord {
  const record: CraftTaskRecord = JSON.parse(text);
  checkDraft(record);
  if (record.last !== null) {
    checkDraft(record.last);
    if (typeof record.last.result !== 'string' || record.last.result.length > 48000) throw new Error('Invalid saved result.');
  }
  return record;
}

export function localTasks(request: typeof fetch, load: () => CraftTaskRecord, store: (record: CraftTaskRecord) => void) {
  let active: AbortController | null = null;
  async function json(endpoint: 'tags' | 'show' | 'generate', signal: AbortSignal, body?: object) {
    const res = await request('http://127.0.0.1:11434/api/' + endpoint, {
      method: body ? 'POST' : 'GET', headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined, signal, redirect: 'error',
    });
    if (!res.ok || !res.body) {
      await res.body?.cancel();
      throw new Error('Local Ollama request failed (' + res.status + ').');
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let text = '', bytes = 0;
    try {
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        bytes += chunk.value.length;
        if (bytes > 512 * 1024) {
          await reader.cancel();
          throw new Error('Local Ollama response exceeded the limit.');
        }
        text += decoder.decode(chunk.value, { stream: true });
      }
    } finally { reader.releaseLock(); }
    return JSON.parse(text + decoder.decode());
  }
  async function models(signal = AbortSignal.timeout(5000)): Promise<string[]> {
    const tags = await json('tags', signal);
    if (!Array.isArray(tags.models)) throw new Error('Invalid local Ollama model list.');
    return tags.models.filter((m: { name?: string; remote_model?: string; remote_host?: string; size?: number }) =>
      m && typeof m.name === 'string' && modelName.test(m.name) && !/:cloud$|-cloud(?::|$)/i.test(m.name) &&
      !m.remote_model && !m.remote_host && typeof m.size === 'number' && m.size > 0
    ).map((m: { name: string }) => m.name);
  }
  function save(draft: CraftDraft) {
    checkDraft(draft);
    if (active) throw new Error('A local task is running.');
    store({ ...load(), prompt: draft.prompt, model: draft.model });
  }
  async function run(draft: CraftDraft) {
    checkDraft(draft);
    if (!draft.prompt.trim() || !draft.model) throw new Error('Enter a task and select a local model.');
    save(draft);
    const controller = new AbortController();
    active = controller;
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(120000)]);
    try {
      if (!(await models(signal)).includes(draft.model)) throw new Error('Select an installed local model.');
      const info = await json('show', signal, { model: draft.model });
      if (info.remote_model || info.remote_host || !info.model_info || !Object.keys(info.model_info).length) {
        throw new Error('This model has no verified local weights.');
      }
      const result = await json('generate', signal, {
        model: draft.model, prompt: draft.prompt, stream: false, think: false, keep_alive: '5m',
        options: { num_predict: 1024, num_ctx: 8192 },
      });
      signal.throwIfAborted();
      if (result.done !== true || typeof result.response !== 'string' || !result.response.trim() || result.response.length > 48000) {
        throw new Error('Local Ollama returned an incomplete or oversized result.');
      }
      const record = { prompt: draft.prompt, model: draft.model, last: { prompt: draft.prompt, model: draft.model, result: result.response } };
      store(record);
      return record;
    } finally { active = null; }
  }
  return { load, save, models, run, cancel: () => active?.abort() };
}
