import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { placeHoverCard } from '../utils/hoverCardPlacement.js';

// Designed hover card for a task tab. Replaces the plain native
// ``title`` string with a structured component: header (status dot
// + id + the agent's badge), the summary, then a labelled row per fact
// (status / branch / repos / pending permission / changes / PR).
//
// Why a portal: the task rows live inside the tasks menu, which scrolls
// (``overflow-y: auto``) — that same clip eats any in-flow absolute
// tooltip. Rendering into ``document.body`` with ``position: fixed``
// escapes it; where it sits is ``placeHoverCard`` (utils/hoverCardPlacement.js).
//
// ``pointer-events: none`` on the card (set in CSS) means the
// tooltip never steals hover and never needs its own dismiss logic
// — it follows the trigger's hover state (``useHoverCard``).
//
// ``placement``: ``beside`` for the tasks menu's rows (left edge), ``below``
// for a tab along the top (a review loop's tab in the centre strip) — under
// it, flipped above when there is no room. ``model.caption`` (optional) is a
// line above the header saying how the trigger relates to the task.

export default function TabTooltip({ anchorRect, model, placement = 'beside' }) {
  const cardRef = useRef(null);
  const [pos, setPos] = useState(null);

  // Measure the card AFTER it renders (its height depends on how
  // many rows the session has), then place it. useLayoutEffect so
  // there's no one-frame flash at 0,0.
  useLayoutEffect(() => {
    if (!anchorRect || !cardRef.current) { return; }
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    setPos(placeHoverCard(anchorRect, cardRef.current.getBoundingClientRect(), viewport, placement));
  }, [anchorRect, model, placement]);

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
      {model.caption && <p className="tab-tooltip-caption">{model.caption}</p>}
      <header className="tab-tooltip-head">
        <span className={`tab-tooltip-dot status-${model.statusKey}`} />
        <strong className="tab-tooltip-id">{model.taskId}</strong>
        {model.agentBadge && (
          <span className={`tab-tooltip-badge is-${model.agentBadge.kind}`}>
            {model.agentBadge.label}
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
