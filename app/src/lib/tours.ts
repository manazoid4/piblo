// One short tour per main page. Plain words; no step says anything is automatic that is not.
export type TourPage = 'home' | 'live' | 'ask' | 'memory' | 'actions' | 'usage' | 'settings';

export interface TourStep {
  title: string;
  body: string;
}

export const TOURS: Record<TourPage, TourStep[]> = {
  home: [
    { title: 'Home', body: 'Your Piblo at a glance: whether it is connected, its battery, and the software on it.' },
    { title: 'Send to Piblo', body: 'Type a note or paste a link and send it to your Piblo. It goes only when you press the button.' },
    { title: 'Jump anywhere', body: 'Press Ctrl+K from any page to jump to a page or reopen a tour.' },
  ],
  live: [
    { title: 'Live View', body: 'This shows your Piblo screen, refreshed about twice a second. It pauses when the window is hidden.' },
    { title: 'Press its buttons', body: 'The buttons under the screen press the same keys as on the device.' },
    { title: 'Type for it', body: 'Type in the box and press Send. Text goes to Piblo only when you press Send.' },
  ],
  ask: [
    { title: 'Ask', body: 'Talk to the same brain your Piblo uses. Type a question and press Enter.' },
    { title: 'Model and voice', body: 'Further down you can see which brain answers and how Piblo speaks.' },
  ],
  memory: [
    { title: 'Memory', body: 'Everything your Piblo heard and everything you saved, in one place.' },
    { title: 'Saved thoughts', body: 'Write a thought, idea or reminder in the box at the top to keep it.' },
    { title: 'Library', body: 'Save a link, or search your library for anything you kept.' },
  ],
  actions: [
    { title: 'Actions', body: 'What needs you, and what you can do right now.' },
    { title: 'Approvals', body: 'Nothing runs until you press Approve. Decline and it is dropped.' },
    { title: 'Focus sprint', body: 'Name what you are focusing on and Piblo helps you stay quiet until it is done.' },
  ],
  usage: [
    { title: 'Usage', body: 'Tokens used on this PC. It refreshes every minute.' },
    { title: 'By service', body: 'Claude, Codex and OpenRouter credits each have their own card. Nothing is spent for you.' },
  ],
  settings: [
    { title: 'Settings', body: 'Pairing, privacy, theme and start-up options are here.' },
    { title: 'Pairing', body: 'Compare the code on Piblo with the code here, then press Allow. Not yours? Press Not mine.' },
    { title: 'Tours', body: 'Press Show page tours again to see every tour once more. Ctrl+K also has a Tour of this page command.' },
  ],
};

const KEY = 'piblo.tours.seen';

export function isTourPage(s: string): s is TourPage {
  return Object.prototype.hasOwnProperty.call(TOURS, s);
}

function read(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

export const tourSeen = (page: TourPage) => read().includes(page);

export function markTourSeen(page: TourPage) {
  try {
    if (!tourSeen(page)) localStorage.setItem(KEY, JSON.stringify([...read(), page]));
  } catch {
    /* storage blocked: the tour just shows again next time */
  }
}

export function resetTours() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to reset */
  }
}

/** Next index when moving by `dir`, clamped to the tour; -1 past the end means "finish". */
export function stepIndex(current: number, dir: 1 | -1, total: number): number {
  const n = current + dir;
  return n < 0 ? 0 : n >= total ? -1 : n;
}
