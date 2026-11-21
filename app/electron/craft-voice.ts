import type { CraftVoiceBridge } from '../shared/types';

export function craftVoice(base: () => string, token: () => string, transport: typeof fetch = fetch): CraftVoiceBridge {
  let capture: string | null = null;
  let arming = false;
  let generation = 0;

  function request(method: string, route = '', id: string | null = null) {
    const headers: Record<string, string> = { Authorization: 'Bearer ' + token() };
    if (id) headers['X-Craft-Capture'] = id;
    return transport(base() + '/craft/voice' + route, {
      method, headers, signal: AbortSignal.timeout(4000), redirect: 'error', cache: 'no-store',
    });
  }
  return {
    async arm() {
      if (capture || arming) throw new Error('A Craft capture is already armed.');
      arming = true;
      const started = generation;
      try {
        const response = await request('POST', '/arm');
        if (response.status === 404) throw new Error('This version of Piblo Core does not support Craft voice.');
        if (!response.ok) throw new Error('Could not arm Craft voice.');
        const data = await response.json();
        const id = data.capture_id;
        if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{32,64}$/.test(id)) throw new Error('Invalid capture receipt.');
        if (started !== generation) {
          await request('DELETE', '', id);
          throw new Error('Capture cancelled.');
        }
        capture = id;
      } finally { arming = false; }
    },
    async read() {
      const id = capture;
      if (!id) return { status: 'expired' };
      const response = await request('GET', '', id);
      if (id !== capture) return { status: 'expired' };
      if (response.status === 404) {
        capture = null;
        return { status: 'expired' };
      }
      if (!response.ok) throw new Error('Could not read the Craft capture.');
      const data = await response.json();
      if (id !== capture) return { status: 'expired' };
      if (data.status === 'waiting') return { status: 'waiting' };
      capture = null;
      if (data.status !== 'ready' || typeof data.text !== 'string' || data.text.length > 12000) {
        throw new Error('Invalid Craft transcript.');
      }
      return { status: 'ready', text: data.text };
    },
    async cancel() {
      generation++;
      const id = capture;
      capture = null;
      if (id) await request('DELETE', '', id);
    },
  };
}
