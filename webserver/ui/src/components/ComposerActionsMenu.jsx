import { useRef, useState } from 'react';
import { CLAUDE_COMMANDS } from '../constants/claudeCommands.js';
import { useDismissOnOutsidePointerOrEscape } from '../hooks/useDismissOnOutsidePointerOrEscape.js';

/**
 * The composer's actions palette — everything that isn't needed on every
 * message, behind one `/` button.
 *
 * The toolbar used to carry a pill per setting (model, effort, ultracode,
 * plan, view-plan…). That row cannot survive a narrow chat pane: the pills
 * are fixed-width, so they overflowed the capsule and drew on top of each
 * other. Widening the pane is not a fix — the pane is resizable by design.
 *
 * So the bar keeps only what you touch per message (attach, this menu, the
 * agent mode, send) and everything else moves in here, grouped and showing
 * its current value on the right — the same shape Claude Code uses.
 */
export default function ComposerActionsMenu({
  onRun,
  disabled = false,
  models = [],
  selectedModel = '',
  onModelChange,
  effortLevels = [],
  selectedEffort = '',
  onEffortChange,
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState('');
  const rootRef = useRef(null);

  function close() {
    setOpen(false);
    setConfirming('');
  }

  useDismissOnOutsidePointerOrEscape(open, close, rootRef);

  function pick(entry) {
    if (entry.destructive && confirming !== entry.command) {
      setConfirming(entry.command);
      return;
    }
    close();
    if (typeof onRun === 'function') { onRun(entry.command); }
  }

  const hasModelRow = models.length > 0 || effortLevels.length > 0;

  return (
    <div className="composer-actions-menu" ref={rootRef}>
      <button
        type="button"
        className={`composer-actions-trigger tooltip-above ${open ? 'is-open' : ''}`}
        data-tooltip="Actions — Claude commands, model, and reasoning effort."
        aria-label="Actions"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <span aria-hidden="true">/</span>
      </button>
      {open && (
        <div className="composer-actions-popover" role="menu">
          <div className="composer-actions-section">Commands</div>
          {CLAUDE_COMMANDS.map((entry) => (
            <button
              key={entry.command}
              type="button"
              role="menuitem"
              className={`composer-actions-item${
                entry.destructive ? ' is-destructive' : ''
              }${confirming === entry.command ? ' is-confirming' : ''}`}
              onClick={() => pick(entry)}
            >
              <span className="composer-actions-item-name">{entry.command}</span>
              <span className="composer-actions-item-desc">
                {confirming === entry.command
                  ? 'Click again to confirm — this erases the conversation.'
                  : entry.description}
              </span>
            </button>
          ))}

          {hasModelRow && (
            <>
              <div className="composer-actions-section">Model</div>
              {models.length > 0 && (
                <div className="composer-actions-row">
                  <span className="composer-actions-row-label">Model</span>
                  <select
                    className="composer-actions-select"
                    aria-label="Select model"
                    value={selectedModel}
                    onChange={(e) => onModelChange && onModelChange(e.target.value)}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>{m.label}</option>
                    ))}
                  </select>
                </div>
              )}
              {effortLevels.length > 0 && (
                <div className="composer-actions-row">
                  <span className="composer-actions-row-label">Effort</span>
                  <select
                    className="composer-actions-select"
                    aria-label="Select reasoning effort"
                    value={selectedEffort}
                    onChange={(e) => onEffortChange && onEffortChange(e.target.value)}
                  >
                    {effortLevels.map((level) => (
                      <option key={level} value={level}>{level}</option>
                    ))}
                  </select>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
