import { useEffect, useRef, useState } from 'react';
import { stepIndex, TOURS, type TourPage } from '../lib/tours';

/**
 * Short guided tour for one page. A modal dialog: focus moves into it, Tab stays inside, Esc skips,
 * arrow keys move between steps, and each step is announced to screen readers.
 */
export function PageTour({ page, onClose }: { page: TourPage; onClose(): void }) {
  const steps = TOURS[page];
  const [i, setI] = useState(0);
  const primary = useRef<HTMLButtonElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const last = i === steps.length - 1;
  const step = steps[i];

  const move = (dir: 1 | -1) => {
    const n = stepIndex(i, dir, steps.length);
    if (n === -1) onClose();
    else setI(n);
  };

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    return () => before?.focus?.();
  }, []);
  useEffect(() => primary.current?.focus(), [i]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'ArrowRight') move(1);
    else if (e.key === 'ArrowLeft') move(-1);
    else if (e.key === 'Tab') {
      const items = Array.from(root.current?.querySelectorAll<HTMLElement>('button') ?? []);
      const first = items[0];
      const end = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        end.focus();
      } else if (!e.shiftKey && document.activeElement === end) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div
        ref={root}
        className="card palette-card stack tour-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-h"
        aria-describedby="tour-b"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <p className="meta" role="status" aria-live="polite">
          Tour {i + 1} of {steps.length}
        </p>
        <h2 id="tour-h">{step.title}</h2>
        <p id="tour-b">{step.body}</p>
        <div className="row">
          <button type="button" className="btn ghost" onClick={onClose}>
            Skip tour
          </button>
          <span className="grow" />
          {i > 0 && (
            <button type="button" className="btn" onClick={() => move(-1)}>
              Back
            </button>
          )}
          <button ref={primary} type="button" className="btn primary" onClick={() => move(1)}>
            {last ? 'Done' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  );
}
