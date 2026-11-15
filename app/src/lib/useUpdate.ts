import { useEffect, useState } from 'react';
import type { UpdateStatus } from '../../shared/types';

/** App self-update: the main process checks and downloads quietly; this only mirrors its state. */
export function useUpdate() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);

  useEffect(() => {
    let live = true;
    void window.piblo.update.status().then((s) => {
      if (live) setStatus(s);
    });
    const off = window.piblo.update.onChange((s) => setStatus(s));
    return () => {
      live = false;
      off();
    };
  }, []);

  return {
    status,
    check: async () => setStatus(await window.piblo.update.check()),
    install: () => window.piblo.update.install(),
  };
}

/** One plain sentence for whatever the updater is doing right now. */
export function describeUpdate(s: UpdateStatus | null): string {
  if (!s) return 'Checking quietly in the background.';
  switch (s.state) {
    case 'disabled':
      return 'Updates are off in this development build.';
    case 'checking':
      return 'Checking for a newer version…';
    case 'downloading':
      return `Downloading ${s.version || 'a new version'}… ${s.progress}%`;
    case 'ready':
      return `${s.version || 'A new version'} is downloaded and ready.`;
    case 'error':
      return 'Could not reach the update server. We will try again later.';
    default:
      return 'You are on the latest version.';
  }
}
