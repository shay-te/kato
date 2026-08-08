import { useRef, useState } from 'react';
import { CLAUDE_COMMANDS } from '../constants/claudeCommands.js';
import { useDismissOnOutsidePointerOrEscape } from '../hooks/useDismissOnOutsidePointerOrEscape.js';

/**
 * Slash-command picker for the composer toolbar.
 *
 * Picking a command SENDS it — these are instructions to the running session
 * (compact it, clear it, report its cost), not text to keep editing, and a
 * half-typed `/compact` sitting in the box helps nobody.
 *
 * `/clear` is the exception: it destroys the conversation the operator has
 * been building, so it confirms first. Kato's whole session model is "one
 * session per task, never restarted behind your back" — a mis-click that
 * silently wipes the history would break that promise just as surely as an
 * automatic restart would.
 */
export default function ComposerCommandMenu({ onRun, disabled = false }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState('');
  const rootRef = useRef(null);

  function close() {
    setOpen(false);
    setConfirming('');
  }

  useDismissOnOutsidePointerOrEscape(open, close);

  function pick(entry) {
    if (entry.destructive && confirming !== entry.command) {
      setConfirming(entry.command);
      return;
    }
    close();
    if (typeof onRun === 'function') { onRun(entry.command); }
  }

  return (
    <div className="composer-command-menu" ref={rootRef}>
      <button
        type="button"
        className={`composer-command-trigger tooltip-above ${open ? 'is-open' : ''}`}
        data-tooltip="Claude commands — the ones that work over kato's connection to the CLI. Picking one sends it to this session."
        aria-label="Claude commands"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <span aria-hidden="true">/</span>
      </button>
      {open && (
        <div className="composer-command-popover" role="menu">
          {CLAUDE_COMMANDS.map((entry) => (
            <button
              key={entry.command}
              type="button"
              role="menuitem"
              className={`composer-command-item${
                entry.destructive ? ' is-destructive' : ''
              }${confirming === entry.command ? ' is-confirming' : ''}`}
              onClick={() => pick(entry)}
            >
              <span className="composer-command-item-name">{entry.command}</span>
              <span className="composer-command-item-desc">
                {confirming === entry.command
                  ? 'Click again to confirm — this erases the conversation.'
                  : entry.description}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
