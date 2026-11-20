import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, expect, it } from 'vitest';
import { PageTour } from '../src/components/PageTour';
import { filterCommands, PALETTE_COMMANDS } from '../src/lib/dev';
import { isTourPage, markTourSeen, resetTours, stepIndex, TOURS, tourSeen } from '../src/lib/tours';

const store = new Map<string, string>();
beforeEach(() => {
  store.clear();
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  };
});

it('every main page has a short tour in plain words', () => {
  for (const page of ['home', 'live', 'ask', 'memory', 'actions', 'usage', 'settings']) {
    expect(isTourPage(page)).toBe(true);
    const steps = TOURS[page as keyof typeof TOURS];
    expect(steps.length).toBeGreaterThanOrEqual(2);
    expect(steps.length).toBeLessThanOrEqual(5);
    for (const s of steps) {
      expect(`${s.title} ${s.body}`).not.toMatch(/\b(Nod|MAZ|Cardputer)\b/);
      expect(s.title.length).toBeGreaterThan(0);
      expect(s.body.length).toBeGreaterThan(0);
    }
  }
  expect(isTourPage('dev')).toBe(false);
  expect(isTourPage('toString')).toBe(false);
});

it('remembers seen tours per page and can reset them', () => {
  expect(tourSeen('home')).toBe(false);
  markTourSeen('home');
  markTourSeen('home');
  expect(tourSeen('home')).toBe(true);
  expect(tourSeen('live')).toBe(false);
  expect(JSON.parse(store.get('piblo.tours.seen')!)).toEqual(['home']);
  resetTours();
  expect(tourSeen('home')).toBe(false);
});

it('survives corrupt or blocked storage', () => {
  store.set('piblo.tours.seen', '{not json');
  expect(tourSeen('home')).toBe(false);
  (globalThis as { localStorage?: unknown }).localStorage = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); } };
  expect(() => markTourSeen('home')).not.toThrow();
  expect(() => resetTours()).not.toThrow();
});

it('steps clamp at the start and finish past the end', () => {
  expect(stepIndex(0, -1, 3)).toBe(0);
  expect(stepIndex(0, 1, 3)).toBe(1);
  expect(stepIndex(2, 1, 3)).toBe(-1);
});

it('renders an accessible, skippable dialog starting at step 1', () => {
  const html = renderToStaticMarkup(<PageTour page="live" onClose={() => {}} />);
  expect(html).toContain('role="dialog"');
  expect(html).toContain('aria-modal="true"');
  expect(html).toContain('aria-labelledby="tour-h"');
  expect(html).toContain('aria-live="polite"');
  expect(html).toContain('Tour 1 of 3');
  expect(html).toContain('Skip tour');
  expect(html).toContain('>Next<');
  expect(html).not.toContain('>Back<');
});

it('can be reopened from Ctrl+K', () => {
  expect(filterCommands(PALETTE_COMMANDS, 'tour').map((c) => c.id)).toEqual(['help:tour']);
  expect(filterCommands(PALETTE_COMMANDS, 'help').map((c) => c.id)).toContain('help:tour');
});
