/**
 * Remaining context-window indicator for the composer toolbar.
 *
 * Kato runs ONE Claude session per task and never restarts it behind the
 * operator's back — so when the window fills, what to do about it is their
 * call, and they can only make it if they can see it coming. That is what
 * this is for.
 *
 * It reports REMAINING rather than used: "12% left" is the number you act on.
 *
 * When the window is unknown (no live session, or a model kato can't size) it
 * says so instead of rendering a percentage. A confident-looking bar built on
 * a guessed window would be worse than no bar — it would send someone into a
 * needless compaction, or let them hit the wall mid-task believing they had
 * room.
 *
 * Given ``onStartChatFromSummary`` the meter is also the way out of a full
 * window: clicking it offers a new chat that starts from a summary of this one.
 * Once the window is low the label says so, so the offer is found before it is
 * needed rather than after.
 *
 * It answers ONE question — how close is the window to full. What the chat
 * COSTS per turn is a different question with its own indicator; see
 * ChatCostDot.jsx.
 */
import { useRef, useState } from 'react';
import { useDismissOnOutsidePointerOrEscape } from '../hooks/useDismissOnOutsidePointerOrEscape.js';
import { formatTokens } from '../utils/chatCost.js';

export default function ContextMeter({
  usage,
  onStartChatFromSummary = null,
  handoffBusy = false,
  turnInFlight = false,
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  useDismissOnOutsidePointerOrEscape(open, () => setOpen(false), rootRef);

  const used = toCount(usage?.used_tokens);
  const limit = toCount(usage?.limit_tokens);
  const known = limit > 0 && used > 0;
  // Nothing to report → render NOTHING. A placeholder in a row of pills is
  // clutter, and an empty gauge is one glance away from being read as a
  // reading. The meter appears once a turn has told us where we stand.
  if (!known) { return null; }
  const remainingPct = Math.max(
    0, Math.min(100, Math.round(((limit - used) / limit) * 100)),
  );
  const level = severity(remainingPct);
  const reading = `Context window: ${remainingPct}% remaining, `
    + `${formatTokens(used)} of ${formatTokens(limit)} used`;
  const gauge = (
    <>
      <span className="context-meter-track" aria-hidden="true">
        <span
          className="context-meter-fill"
          style={{ width: `${100 - remainingPct}%` }}
        />
      </span>
      <span className="context-meter-label">{`${remainingPct}% left`}</span>
    </>
  );
  // ``tooltip-above tooltip-end``: this sits at the bottom-right corner of the
  // composer, where the default (below, centred) tooltip renders underneath
  // the chat input and off the right edge — unreadable.
  const meterClass = `context-meter context-meter--${level} tooltip-above tooltip-end`;

  if (typeof onStartChatFromSummary !== 'function') {
    return (
      <div
        className={meterClass}
        role="status"
        aria-label={reading}
        data-tooltip={tooltip(used, limit, remainingPct, false)}
      >
        {gauge}
      </div>
    );
  }

  function start() {
    setOpen(false);
    onStartChatFromSummary();
  }

  return (
    <div className="context-meter-wrap" ref={rootRef}>
      <button
        type="button"
        className={`${meterClass} is-actionable`}
        aria-label={`${reading}. Start a new chat from a summary`}
        aria-haspopup="menu"
        aria-expanded={open}
        data-tooltip={tooltip(used, limit, remainingPct, true)}
        onClick={() => setOpen((was) => !was)}
      >
        {gauge}
        {level !== 'ok' && (
          <span className="context-meter-offer">· new chat</span>
        )}
      </button>
      {open && (
        <div className="composer-mode-popover" role="menu">
          <div className="composer-mode-popover-title">Context window</div>
          {turnInFlight && !handoffBusy && (
            <div className="composer-mode-popover-note">
              Available once the current turn finishes.
            </div>
          )}
          <button
            type="button"
            role="menuitem"
            className="composer-mode-item"
            onClick={start}
            disabled={handoffBusy || turnInFlight}
          >
            <span className="composer-mode-item-icon" aria-hidden="true">↻</span>
            <span className="composer-mode-item-text">
              <span className="composer-mode-item-name">
                {handoffBusy ? 'Writing the summary…' : 'New chat from a summary'}
              </span>
              <span className="composer-mode-item-desc">
                The agent summarises this chat, then kato opens a fresh chat
                that starts from that summary. This chat stays in the chats menu.
              </span>
            </span>
          </button>
        </div>
      )}
    </div>
  );
}




// Warn early enough to act. A summary or a compaction takes a turn, so "act
// now" has to fire while there is still room to run it.
function severity(remainingPct) {
  if (remainingPct <= 10) { return 'critical'; }
  if (remainingPct <= 25) { return 'low'; }
  return 'ok';
}

function tooltip(used, limit, remainingPct, canStartChat) {
  const base = `Context window: ${formatTokens(used)} of ${formatTokens(limit)} `
    + `used, ${remainingPct}% left.`;
  const newChat = canStartChat
    ? ' Click to start a new chat from a summary of this one.'
    : '';
  if (remainingPct > 25) {
    return `${base}${newChat} Kato keeps ONE session per task and never `
      + 'restarts it on its own — run /compact yourself when this gets low.';
  }
  return `${base}${newChat} Or run /compact from the commands menu to `
    + 'summarise the conversation and keep going in the same session.';
}

function toCount(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.round(number) : 0;
}
