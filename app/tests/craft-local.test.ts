import { describe, expect, it, vi } from 'vitest';
import { emptyTask, localTasks, parseTask } from '../electron/craft-local';

const draft = { prompt: 'Draft one sentence.', model: 'sample:latest' };
const row = { name: draft.model, size: 200 };
function fixture(request: typeof fetch) {
  let encryptedPayload = JSON.stringify(emptyTask);
  return localTasks(request, () => parseTask(encryptedPayload), value => { encryptedPayload = JSON.stringify(value); });
}
function ollama(answer: (path: string, body: Record<string, unknown>) => unknown) {
  return vi.fn<typeof fetch>(async (url, init) => {
    const target = new URL(String(url));
    expect(target.origin).toBe('http://127.0.0.1:11434');
    expect(init?.redirect).toBe('error');
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    return Response.json(answer(target.pathname, body));
  });
}
describe('Craft local tasks', () => {
  it('restores a draft and reviewed output without issuing another request', async () => {
    const request = ollama((path, body) => {
      if (path === '/api/tags') return { models: [row, { ...row, name: 'remote', remote_host: 'https://example.com' }] };
      if (path === '/api/show') return { model_info: { architecture: 'sample' } };
      expect(body).toMatchObject({ ...draft, stream: false, think: false, keep_alive: '5m', options: { num_predict: 1024, num_ctx: 8192 } });
      return { done: true, response: 'Local result.' };
    });
    const tasks = fixture(request);
    tasks.save(draft);
    expect(tasks.load().prompt).toBe(draft.prompt);
    expect(await tasks.models()).toEqual([draft.model]);
    await tasks.run(draft);
    tasks.save({ ...draft, prompt: 'Next draft.' });
    expect(tasks.load()).toMatchObject({ prompt: 'Next draft.', last: { ...draft, result: 'Local result.' } });
    expect(request).toHaveBeenCalledTimes(4);
  });
  it('rejects an alias for a remote model before sending text', async () => {
    const request = ollama(path => path === '/api/tags' ? { models: [row] } : { remote_model: 'upstream', model_info: { size: 20 } });
    await expect(fixture(request).run(draft)).rejects.toThrow(/local weights/);
    expect(request.mock.calls.some(([url]) => String(url).endsWith('/generate'))).toBe(false);
  });
  it('aborts in flight and leaves only the draft', async () => {
    let started!: () => void;
    const waiting = new Promise<void>(resolve => { started = resolve; });
    const request: typeof fetch = async (url, opts) => {
      if (String(url).endsWith('/tags')) return Response.json({ models: [row] });
      if (String(url).endsWith('/show')) return Response.json({ model_info: { size: 2 } });
      started();
      return new Promise((_resolve, reject) => opts!.signal!.addEventListener('abort', () => reject(new Error('canceled')), { once: true }));
    };
    const tasks = fixture(request);
    const result = tasks.run(draft);
    await waiting;
    await expect(tasks.run(draft)).rejects.toThrow(/running/);
    tasks.cancel();
    await expect(result).rejects.toThrow('canceled');
    expect(tasks.load()).toEqual({ ...draft, last: null });
  });
  it('refuses oversized service output and malformed saved records', async () => {
    const request = ollama(path => path === '/api/tags' ? { models: [row] } : path === '/api/show' ? { model_info: { size: 1 } } : { done: true, response: 'a'.repeat(550000) });
    const tasks = fixture(request);
    await expect(tasks.run(draft)).rejects.toThrow(/limit/);
    expect(tasks.load().last).toBeNull();
    expect(() => parseTask('{"model":5}')).toThrow();
  });
});
