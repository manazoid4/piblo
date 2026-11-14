import { useEffect, useRef, useState } from 'react';
import { filterCommands, PALETTE_COMMANDS, type PaletteCommand } from '../lib/dev';
import { Icon } from './ui';

export function CommandPalette({
  open,
  onClose,
  onSelect,
}: {
  open: boolean;
  onClose(): void;
  onSelect(cmd: PaletteCommand): void;
}) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
    const commands = import.meta.env.DEV ? PALETTE_COMMANDS : PALETTE_COMMANDS.filter((c) => c.group !== 'Piblo Dev');
    const items = filterCommands(commands, query);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActiveIndex(0);
      window.setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  if (!open) return null;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => (items.length ? (i + 1) % items.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => (items.length ? (i - 1 + items.length) % items.length : 0));
    } else if (e.key === 'Enter' && items[activeIndex]) {
      e.preventDefault();
      onSelect(items[activeIndex]);
      onClose();
    }
  };

  return (
    <div className="palette-backdrop" role="dialog" aria-modal="true" aria-label="Jump anywhere" onClick={onClose}>
      <div className="card palette-card stack" onClick={(e) => e.stopPropagation()} onKeyDown={handleKeyDown}>
        <div className="row">
          <Icon name="search" size={18} />
          <input
            ref={inputRef}
            className="input"
            placeholder="Jump anywhere in Piblo..."
            aria-label="Jump anywhere"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIndex(0);
            }}
          />
          <button type="button" className="btn sm ghost" onClick={onClose}>
            Esc
          </button>
        </div>
        <div className="palette-list" role="listbox" aria-label="Commands">
          {items.length ? (
            items.map((cmd, idx) => (
              <button
                key={cmd.id}
                type="button"
                role="option"
                aria-selected={idx === activeIndex}
                className={`palette-item ${idx === activeIndex ? 'active' : ''}`}
                onClick={() => {
                  onSelect(cmd);
                  onClose();
                }}
              >
                <span className="tag">{cmd.group}</span>
                <span className="grow">{cmd.label}</span>
                {cmd.shortcut && <kbd>{cmd.shortcut}</kbd>}
              </button>
            ))
          ) : (
            <p className="meta" style={{ padding: 'var(--s-3)' }}>
              No matching places found.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
