import { useRef, useState } from 'react';
import { AGENT_MODES, agentModeEntry } from '../constants/agentModes.js';
import { useDismissOnOutsidePointerOrEscape } from '../hooks/useDismissOnOutsidePointerOrEscape.js';

/**
 * Agent-mode picker for the composer toolbar — Claude Code's Modes menu.
 *
 * Replaces the standalone "Plan mode" toggle: plan is one of four modes, and
 * a single control says which one is active instead of a button that only
 * tells you about one of them.
 *
 * The trigger always shows the mode that will actually run, never a generic
 * "Mode" label — the picker's job is to answer "what will happen when I hit
 * send", and an ambiguous label is exactly the thing that gets an operator to
 * approve an edit they thought needed confirming.
 *
 * It also hosts the two other agent-behaviour controls — the ultracode
 * opt-in and "View plan". They were standalone pills, which is how the
 * toolbar grew wide enough to overlap itself on a narrow window. They belong
 * here anyway: all three answer "how should the agent behave", and neither is
 * touched often enough to earn permanent space.
 */
export default function ComposerModeMenu({
  mode,
  onChange,
  disabled = false,
  ultracode = false,
  onUltracodeChange,
  supportsWorkflows = false,
  planAvailable = false,
  onOpenPlan,
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const active = agentModeEntry(mode);

  useDismissOnOutsidePointerOrEscape(open, () => setOpen(false), rootRef);

  function pick(entry) {
    setOpen(false);
    if (entry.mode !== active.mode && typeof onChange === 'function') {
      onChange(entry.mode);
    }
  }

  return (
    <div className="composer-mode-menu" ref={rootRef}>
      <button
        type="button"
        className={`composer-mode-trigger tooltip-above tooltip-start ${open ? 'is-open' : ''}`}
        data-tooltip={
          `Agent mode: ${active.label} — ${active.description}. `
          + 'Applies on your next message (kato re-spawns the subprocess and '
          + 'resumes the same session).'
        }
        aria-label={`Agent mode: ${active.label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((was) => !was)}
      >
        <span className="composer-mode-icon" aria-hidden="true">{active.icon}</span>
        <span className="composer-mode-label">{active.label}</span>
      </button>
      {open && (
        <div className="composer-mode-popover" role="menu">
          <div className="composer-mode-popover-title">Modes</div>
          {AGENT_MODES.map((entry) => (
            <button
              key={entry.label}
              type="button"
              role="menuitemradio"
              aria-checked={entry.mode === active.mode}
              className={`composer-mode-item${
                entry.mode === active.mode ? ' is-active' : ''
              }`}
              onClick={() => pick(entry)}
            >
              <span className="composer-mode-item-icon" aria-hidden="true">
                {entry.icon}
              </span>
              <span className="composer-mode-item-text">
                <span className="composer-mode-item-name">{entry.label}</span>
                <span className="composer-mode-item-desc">{entry.description}</span>
              </span>
              {entry.mode === active.mode && (
                <span className="composer-mode-item-check" aria-hidden="true">✓</span>
              )}
            </button>
          ))}
          {(supportsWorkflows || planAvailable) && (
            <div className="composer-mode-extras">
              {supportsWorkflows && (
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={ultracode}
                  className={`composer-mode-item${ultracode ? ' is-active' : ''}`}
                  onClick={() => {
                    if (typeof onUltracodeChange === 'function') {
                      onUltracodeChange(!ultracode);
                    }
                  }}
                >
                  <span className="composer-mode-item-icon" aria-hidden="true">⚡</span>
                  <span className="composer-mode-item-text">
                    <span className="composer-mode-item-name">ultracode</span>
                    <span className="composer-mode-item-desc">
                      Run multi-agent workflows for the next message — can fan
                      out into many subagents
                    </span>
                  </span>
                  {ultracode && (
                    <span className="composer-mode-item-check" aria-hidden="true">✓</span>
                  )}
                </button>
              )}
              {planAvailable && (
                <button
                  type="button"
                  role="menuitem"
                  className="composer-mode-item"
                  onClick={() => {
                    setOpen(false);
                    if (typeof onOpenPlan === 'function') { onOpenPlan(); }
                  }}
                >
                  <span className="composer-mode-item-icon" aria-hidden="true">☰</span>
                  <span className="composer-mode-item-text">
                    <span className="composer-mode-item-name">View plan</span>
                    <span className="composer-mode-item-desc">
                      Open the agent&apos;s latest plan in the centre pane
                    </span>
                  </span>
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
