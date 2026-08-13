import { useLayoutEffect, useRef, useState } from 'react';
import { cx } from '../utils/cx.js';
import { deriveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';
import { deriveAgentStatus, badgeKindFor } from '../utils/agentStatus.js';
import Icon from './Icon.jsx';
import { useResizable } from '../hooks/useResizable.js';

// Resize drives ``max-width``, NOT ``width``. A tab is inline-flex and sizes
// to its content; ``max-width: 260px`` is the cap that ellipsises long names.
// Raising the cap lets the tab grow to fit the name, lowering it shrinks
// toward the id — which is exactly the requested behaviour, and it works WITH
// the strip's layout instead of overriding width/flex-basis and fighting it
// (that fought ``flex-shrink: 0`` and collapsed tabs to their id).
const TAB_CAP_MIN = 132;   // roughly the task code alone
const TAB_CAP_MAX = 600;
const TAB_CAP_DEFAULT = 260;  // the stylesheet's own cap
import { MAX_TAB_NAME_LENGTH } from '../utils/taskTabNames.js';

// Fallbacks used until the label has been measured (first paint, or a
// browser that gives us zero widths). Generous enough that a tab is never
// unusably narrow, and never narrower than its own id.
import TabTooltip from './TabTooltip.jsx';

// Delay before the hover card appears — long enough that scrubbing
// across the strip to reach a far tab doesn't flash a card on every
// tab it passes over.
const HOVER_DELAY_MS = 350;

export default function Tab({
  session, active, needsAttention, liveStatus = null, pinned = false,
  onSelect, onForget, onTogglePin, displayName = '', onRename,
}) {
  const baseStatus = deriveTabStatus(session);
  // The agent dot + tooltip badge derive from the SAME value as the header chip
  // (UNA-2492). liveStatus is read from agentStatusStore by App/TabList.
  const agent = deriveAgentStatus(session, liveStatus, needsAttention);
  const className = cx(
    'tab',
    active && 'active',
    needsAttention && 'needs-attention',
    pinned && 'is-pinned',
  );
  const dotClass = agent.dotClass;
  // Local rename: the label shown is the operator's override when set, else
  // the ticket summary. Renaming never touches the ticket — see taskTabNames.
  const [renaming, setRenaming] = useState(false);
  const label = String(
    displayName || session?.task_summary || '',
  ).trim();

  function commitRename(value) {
    setRenaming(false);
    if (typeof onRename !== 'function') { return; }
    // A blank clears the override and falls back to the ticket summary.
    onRename(session.task_id, String(value == null ? '' : value).trim());
  }

  // Hover-card state. ``anchorRect`` is a frozen snapshot of the
  // <li>'s viewport rect taken when the card opens — TabTooltip
  // positions itself off it (and re-measures its own height).
  const liRef = useRef(null);
  const timerRef = useRef(null);
  const [anchorRect, setAnchorRect] = useState(null);

  function openTooltipSoon() {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      if (liRef.current) {
        setAnchorRect(liRef.current.getBoundingClientRect());
      }
    }, HOVER_DELAY_MS);
  }
  function closeTooltip() {
    clearTimeout(timerRef.current);
    setAnchorRect(null);
  }

  // Selecting is the <li>'s job, but the pill also carries controls (pin, ×,
  // the resize handle, the rename box) that must NOT select. Each of those
  // stops propagation itself; this is the backstop, so a control added later
  // can't silently reintroduce "clicking the × opened the task" — and so a
  // click that lands on the pill while the pointer sits over a control still
  // does nothing rather than switching tasks under the operator.
  const CONTROL_SELECTOR = 'button, input, .tab-resize-handle';

  function handleSelect(event) {
    if (event?.target?.closest?.(CONTROL_SELECTOR)) { return; }
    closeTooltip();
    onSelect(session.task_id);
  }
  function handleForget(event) {
    event.stopPropagation();
    closeTooltip();
    if (typeof onForget !== 'function') { return; }
    // Don't act here — hand off to App, which opens the
    // ForgetTaskModal hard-confirm. Forgetting is destructive, so
    // the operator must explicitly approve it in that dialog.
    onForget(session.task_id);
  }
  function handleTogglePin(event) {
    event.stopPropagation();
    closeTooltip();
    if (typeof onTogglePin !== 'function') { return; }
    onTogglePin(session.task_id);
  }

  const hasChangesPending = !!session.has_changes_pending;
  const changesIndicator = hasChangesPending && (
    <span className="tab-changes-indicator" aria-hidden="true">
      <Icon name="commit" />
    </span>
  );

  // The tab shows the task NAME next to the id (VSCode/Chrome style) so the
  // operator can tell tabs apart WITHOUT hovering for the tooltip. The id
  // stays (short, stable, never truncated); the name is the wide part and
  // ellipsises when the tab hits its max-width. The hover card still carries
  // the untruncated name + the rest of the detail.
  const summary = String(session?.task_summary || '').trim();

  const capKey = `kato.tab.cap.${session.task_id}`;
  const { width: cap, onPointerDown } = useResizable({
    storageKey: capKey,
    defaultWidth: TAB_CAP_DEFAULT,
    minWidth: TAB_CAP_MIN,
    maxWidth: TAB_CAP_MAX,
    // 'left' so dragging RIGHT widens. With 'right' the delta is inverted
    // for a handle on the tab's right edge — pulling left grew the tab.
    anchor: 'left',
  });

  const model = buildTooltipModel(session, baseStatus, needsAttention, agent);

  return (
    <>
      <li
        ref={liRef}
        className={className}
        // Always applied: an untouched tab's cap equals the stylesheet's own
        // 260px, so this is a no-op until dragged. Gating it behind state made
        // pointerdown re-render mid-drag, which dropped pointer capture — the
        // tab then resized on plain mouse MOVE, with no button held.
        style={{ maxWidth: `${cap}px` }}
        data-task-id={session.task_id}
        onClick={handleSelect}
        onMouseEnter={openTooltipSoon}
        onMouseLeave={closeTooltip}
        // Keyboard parity: focusing the tab (tab-key nav) also
        // surfaces the card.
        onFocus={openTooltipSoon}
        onBlur={closeTooltip}
        tabIndex={0}
      >
        <span className={dotClass} />
        <span className="tab-label">
          <strong className="tab-label-id">{session.task_id}</strong>
          {renaming ? (
            <input
              className="tab-label-rename"
              defaultValue={label}
              autoFocus
              maxLength={MAX_TAB_NAME_LENGTH}
              aria-label={`Rename the ${session.task_id} tab`}
              // The tab itself selects-on-click; without this, clicking
              // into your own text box switches tasks under you.
              onClick={(e) => e.stopPropagation()}
              onMouseDown={(e) => e.stopPropagation()}
              onBlur={(e) => commitRename(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { commitRename(e.target.value); }
                // Escape restores the previous label — including an empty
                // one, so a mis-typed rename is always undoable.
                if (e.key === 'Escape') { setRenaming(false); }
                e.stopPropagation();
              }}
            />
          ) : label ? (
            <span
              className="tab-label-title"
              // Double-click is the VSCode-ish gesture and stays out of the
              // way of the single-click select.
              onDoubleClick={(e) => {
                if (typeof onRename !== 'function') { return; }
                e.stopPropagation();
                setRenaming(true);
              }}
              title="Double-click to rename this tab"
            >
              {label}
            </span>
          ) : null}
        </span>
        {changesIndicator}
        <button
          type="button"
          className={cx('tab-pin-btn', pinned && 'is-pinned')}
          aria-label={pinned ? 'Unpin this task' : 'Pin this task'}
          aria-pressed={pinned}
          title={pinned ? 'Unpin tab' : 'Pin tab to the left'}
          onClick={handleTogglePin}
        >
          <Icon name="pin" />
        </button>
        <button
          type="button"
          className="tab-forget-btn"
          aria-label="Forget this task"
          onClick={handleForget}
        >
          <Icon name="xmark" />
        </button>
        <span
          className="tab-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label={`Resize the ${session.task_id} tab`}
          title="Drag to resize this tab"
          onClick={(e) => e.stopPropagation()}
          // MOUSE down, not pointer down: useResizable ends the drag on a
          // document ``mouseup``, and its ``preventDefault()`` on a
          // pointerdown suppresses the compatibility mouse events — so the
          // release was never seen and the tab kept resizing until the next
          // click.
          onMouseDown={(e) => { e.stopPropagation(); onPointerDown(e); }}
        />
      </li>
      {anchorRect && (
        <TabTooltip anchorRect={anchorRect} model={model} />
      )}
    </>
  );
}


// Structured tooltip model — every fact the old ` · `-joined string
// carried, now as discrete fields the card renders as a header +
// labelled rows.
function buildTooltipModel(session, baseStatus, needsAttention, agent) {
  const taskId = String(session?.task_id || '').trim() || 'Task';
  const summary = String(session?.task_summary || '').trim();
  const rows = [];

  const statusLine = tabStatusTitle(baseStatus, needsAttention);
  if (statusLine) {
    rows.push({ label: 'Status', value: statusLine });
  }

  const branch = String(session?.branch_name || '').trim();
  if (branch) { rows.push({ label: 'Branch', value: branch }); }

  const repoIds = Array.isArray(session?.repository_ids)
    ? session.repository_ids.filter(Boolean)
    : [];
  if (repoIds.length === 1) {
    rows.push({ label: 'Repo', value: repoIds[0] });
  } else if (repoIds.length > 1) {
    rows.push({
      label: `Repos (${repoIds.length})`,
      value: repoIds.join(', '),
    });
  }

  if (session?.has_pending_permission) {
    const tool = String(session?.pending_permission_tool_name || '').trim();
    rows.push({
      label: 'Permission',
      value: tool ? `Awaiting decision for ${tool}` : 'Awaiting your decision',
      tone: 'warn',
    });
  }
  if (session?.has_changes_pending) {
    rows.push({
      label: 'Changes',
      value: 'Ready to push — waiting for your approval',
      tone: 'warn',
    });
  }
  const pushedPr = String(
    session?.pr_url || session?.pull_request_url || '',
  ).trim();
  if (pushedPr) { rows.push({ label: 'PR', value: pushedPr }); }

  return {
    taskId,
    summary,
    statusKey: agent.status,
    claudeBadge: agentTooltipBadge(agent),
    rows,
  };
}


// Compact Claude liveness badge for the hover card — now derived from the
// shared agent status (utils/agentStatus.js) so the card, the tab dot, and the
// chat header all speak the same language (UNA-2492). Returns null for kinds
// with no badge styling (provisioning/missing/unknown), matching the old
// claudeBadge's null.
function agentTooltipBadge(agent) {
  const kind = badgeKindFor(agent.kind);
  if (!kind) { return null; }
  return { kind, label: `Claude: ${agent.label}` };
}
