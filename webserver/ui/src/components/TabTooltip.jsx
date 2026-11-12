import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// Designed hover card for a task tab. Replaces the plain native
// ``title`` string with a structured component: header (status dot
// + id + Claude badge), the summary, then a labelled row per fact
// (status / branch / repos / pending permission / changes / PR).
//
// Why a portal: the task rows live inside the tasks menu, which scrolls
// (``overflow-y: auto``) — that same clip eats any in-flow absolute
// tooltip. Rendering into ``document.body`` with ``position: fixed``
// escapes it; the card sits BESIDE the row (the menu is on the left
// edge, so beside means to its right), flips to the left when there is
// no room, and is clamped vertically so it never leaves the viewport.
//
// ``pointer-events: none`` on the card (set in CSS) means the
// tooltip never steals hover and never needs its own dismiss logic
// — it follows the trigger's hover state, owned by ``Tab``.

const GAP = 8;          // px between trigger and card
const VIEWPORT_PAD = 8; // keep this far from any viewport edge

export default function TabTooltip({ anchorRect, model }) {
  const cardRef = useRef(null);
  const [pos, setPos] = useState(null);

  // Measure the card AFTER it renders (its height depends on how
  // many rows the session has), then place it. useLayoutEffect so
  // there's no one-frame flash at 0,0.
  useLayoutEffect(() => {
    if (!anchorRect || !cardRef.current) { return; }
    const card = cardRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let left = anchorRect.right + GAP;
    let placement = 'right';
    // Flip to the left of the row if it would overflow the right edge.
    if (left + card.width + VIEWPORT_PAD > vw) {
      left = anchorRect.left - GAP - card.width;
      placement = 'left';
    }
    left = Math.max(VIEWPORT_PAD, left);

    let top = anchorRect.top;
    if (top + card.height + VIEWPORT_PAD > vh) {
      top = vh - card.height - VIEWPORT_PAD;
    }
    top = Math.max(VIEWPORT_PAD, top);

    setPos({ top, left, placement });
  }, [anchorRect, model]);

  if (!anchorRect || !model) { return null; }

  const card = (
    <div
      ref={cardRef}
      className={`tab-tooltip ${pos ? `is-${pos.placement}` : 'is-measuring'}`}
      style={pos
        ? { top: `${pos.top}px`, left: `${pos.left}px` }
        : { top: '-9999px', left: '-9999px' }}
      role="tooltip"
    >
      <header className="tab-tooltip-head">
        <span className={`tab-tooltip-dot status-${model.statusKey}`} />
        <strong className="tab-tooltip-id">{model.taskId}</strong>
        {model.claudeBadge && (
          <span className={`tab-tooltip-badge is-${model.claudeBadge.kind}`}>
            {model.claudeBadge.label}
          </span>
        )}
      </header>

      {model.summary && (
        <p className="tab-tooltip-summary">{model.summary}</p>
      )}

      {model.rows.length > 0 && (
        <dl className="tab-tooltip-rows">
          {model.rows.map((row) => (
            <div className="tab-tooltip-row" key={row.label}>
              <dt>{row.label}</dt>
              <dd className={row.tone ? `tone-${row.tone}` : ''}>
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );

  return createPortal(card, document.body);
}
