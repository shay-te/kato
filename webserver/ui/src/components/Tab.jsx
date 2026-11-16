import { memo, useRef, useState } from 'react';
import { cx } from '../utils/cx.js';
import { AUTOMATION_LEVEL } from '../constants/automationLevel.js';
import PlanQueueTabBadge from './planQueue/PlanQueueTabBadge.jsx';
import { withoutSubtaskPrefix } from './planQueue/subtaskLabel.js';
import { parentOfTab, subtaskListId } from '../utils/subtaskTabs.js';
import { isReviewLoopRunning } from './reviewLoop/reviewLoopHelpers.js';
import ReviewLoopTabBadge from './reviewLoop/ReviewLoopTabBadge.jsx';
import { deriveTabStatus } from '../utils/tabStatus.js';
import { deriveAgentStatus } from '../utils/agentStatus.js';
import Icon from './Icon.jsx';
import { MAX_TAB_NAME_LENGTH } from '../utils/taskTabNames.js';
import TabTooltip from './TabTooltip.jsx';
import { useHoverCard } from '../hooks/useHoverCard.js';
import { buildTaskTooltipModel } from '../utils/taskTooltipModel.js';

// Sub-pixel element width.
//
// NOT ``offsetWidth``: that rounds to a whole pixel, and rounding UP by a
// fraction is enough to make the rename input a hair wider than the label
// holding it — whereupon the label's ``overflow: hidden`` shaves the input's
// 1px right border and the field reads as "cut off on the right". The
// content is never clipped, only the border, which is what made it look like
// a sizing bug rather than a rounding one.
//
// Falls back to ``offsetWidth`` where getBoundingClientRect reports nothing
// (jsdom does no layout, so tests stub the older property).
function measureWidth(node) {
  if (!node) { return 0; }
  const rect = typeof node.getBoundingClientRect === 'function'
    ? node.getBoundingClientRect()
    : null;
  return (rect && rect.width) || node.offsetWidth || 0;
}

// Pixels the rename box stays shy of the label it fills. Sizing it to EXACTLY
// the measured width is not enough: a box flush against its container's edge
// still loses its 1px border to sub-pixel accumulation, so the field reads as
// cut off on the right no matter how precise the measurement. Sitting 2px
// short costs nothing visually and cannot be clipped. The renaming label also
// drops its ``overflow: hidden`` (see app.scss) — there is no text to
// ellipsise once the box is a single fixed-size input.
const RENAME_BOX_INSET_PX = 2;

function Tab({
  session, active, needsAttention, liveStatus = null, pinned = false,
  onSelect, onForget, onTogglePin, displayName = '', onRename,
  // Drag-to-reorder, driven entirely by TabList (which owns the order and
  // the pinned set). Defaults keep the tab inert for any caller that does
  // not wire it — the strip is the only one that does.
  dragging = false, dropTarget = false, canDrop = false,
  onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop,
  // A sub-task row in the tasks menu, drawn under its main task.
  nested = false,
  // A main task's sub-task group: how many, shown or folded away, and how
  // many of the folded ones wait for you. 0 = no group, no toggle.
  subtaskCount = 0, subtasksShown = true, subtasksWaiting = 0, onToggleSubtasks,
}) {
  const baseStatus = deriveTabStatus(session);
  // The agent dot + tooltip badge derive from the SAME value as the header chip
  // (UNA-2492). liveStatus is read from agentStatusStore by App/TabList.
  const agent = deriveAgentStatus(session, liveStatus, needsAttention);
  const dotClass = agent.dotClass;
  // Local rename: the label shown is the operator's override when set, else
  // the ticket summary. Renaming never touches the ticket — see taskTabNames.
  const [renaming, setRenaming] = useState(false);
  // Width the LABEL occupied when the edit started, handed to the input as an
  // explicit pixel width. Sizing the input with percentages instead meant its
  // box ran on underneath the pin and × — which paint above it (z-index: 2),
  // so the field looked like it simply ended there. Reserving space with a
  // ``calc(100% - Npx)`` subtrahend can't fix that reliably: N has to track
  // every sibling and margin in the row, and it silently mis-sizes the moment
  // one changes. The label's own measured width already accounts for all of
  // them, so the input inherits a number that is correct by construction.
  const [renameBoxWidth, setRenameBoxWidth] = useState(0);
  const labelRef = useRef(null);
  // A plan's sub-task: "Subtask of UNA-3267:" is a fixed mark beside the id,
  // never part of the name — so a rename edits only the name after it. A
  // local sub-task (named "UNA-3267 · Task 2: …") gets the mark too.
  const parentTaskId = parentOfTab(session);
  const label = withoutSubtaskPrefix(String(
    displayName || session?.task_summary || '',
  ).trim());

  function startRename() {
    const labelWidth = measureWidth(labelRef.current);
    setRenameBoxWidth(
      labelWidth ? Math.max(0, labelWidth - RENAME_BOX_INSET_PX) : 0,
    );
    setRenaming(true);
  }

  function endRename() {
    setRenaming(false);
    setRenameBoxWidth(0);
  }

  function commitRename(value) {
    endRename();
    if (typeof onRename !== 'function') { return; }
    // A blank clears the override and falls back to the ticket summary.
    onRename(session.task_id, String(value == null ? '' : value).trim());
  }

  // The hover card (useHoverCard: shared with a review loop's tab).
  const { ref: liRef, anchorRect, open: openTooltipSoon, close: closeTooltip } = useHoverCard();

  // Selecting is the <li>'s job, but the row also carries controls (pencil,
  // pin, ×, the rename box) that must NOT select. Each of those
  // stops propagation itself; this is the backstop, so a control added later
  // can't silently reintroduce "clicking the × opened the task" — and so a
  // click that lands on the pill while the pointer sits over a control still
  // does nothing rather than switching tasks under the operator.
  const CONTROL_SELECTOR = 'button, input';

  // Where the press STARTED. A click is dispatched at the nearest common
  // ancestor of the mousedown and mouseup targets, so a press that begins on
  // the × but ends a pixel outside it arrives here on the <li> instead — and
  // selecting the task is the one thing the operator did not want.
  //
  // Belt and braces for the scale(0.97) hit-test bug (see .tab:active in
  // app.scss). That transform is gone, but sub-pixel layout, a smooth-scrolling
  // strip or a slightly moved finger can still retarget a click, and the cost
  // of getting it wrong is destructive-looking: the wrong task opens.
  //
  // It DISPATCHES rather than suppresses. Swallowing the click would stop the
  // task switch but leave the press dead, and the operator asked for both:
  // never open the task, AND get the dialog on the first press.
  const pressedForgetRef = useRef(false);

  function handleSelect(event) {
    if (pressedForgetRef.current) {
      pressedForgetRef.current = false;
      closeTooltip();
      if (typeof onForget === 'function') { onForget(session.task_id); }
      return;
    }
    if (event?.target?.closest?.(CONTROL_SELECTOR)) { return; }
    closeTooltip();
    onSelect(session.task_id);
  }
  function handleForget(event) {
    event.stopPropagation();
    pressedForgetRef.current = false;  // consumed on the normal path
    closeTooltip();
    if (typeof onForget !== 'function') { return; }
    // Don't act here — hand off to App, which opens the
    // ForgetTaskModal hard-confirm. Forgetting is destructive, so
    // the operator must explicitly approve it in that dialog.
    onForget(session.task_id);
  }
  function handleRename(event) {
    event.stopPropagation();
    closeTooltip();
    startRename();
  }
  function handleToggleSubtasks(event) {
    event.stopPropagation();
    closeTooltip();
    onToggleSubtasks(session.task_id);
  }
  const subtasksNoun = subtaskCount === 1 ? 'sub-task' : 'sub-tasks';
  const subtasksToggleLabel = `${subtasksShown ? 'Hide' : 'Show'} the ${subtaskCount} ${subtasksNoun} of ${session.task_id}${
    subtasksWaiting ? ` — ${subtasksWaiting} waiting for you` : ''}`;
  function handleTogglePin(event) {
    event.stopPropagation();
    closeTooltip();
    if (typeof onTogglePin !== 'function') { return; }
    onTogglePin(session.task_id);
  }

  // No unpushed-changes glyph on the tab. It sat between the task name and
  // the tab's buttons as an unlabelled symbol with no hover text — operators
  // read it as noise and asked twice what it meant. The information still
  // exists where it changes a decision: the forget-task dialog warns about
  // unpushed work before it lets you delete the workspace.

  // The tab shows the task NAME next to the id (VSCode/Chrome style) so the
  // operator can tell tabs apart WITHOUT hovering for the tooltip. The id
  // stays (short, stable, never truncated); the name is the wide part and
  // ellipsises when the tab hits its max-width. The hover card still carries
  // the untruncated name + the rest of the detail.
  const summary = String(session?.task_summary || '').trim();

  const className = cx(
    'tab',
    active && 'active',
    needsAttention && 'needs-attention',
    pinned && 'is-pinned',
    nested && 'is-nested',
    dragging && 'dragging',
    dropTarget && 'drop-target',
    // A running review loop rings the whole pill in its own colour. A
    // SEPARATE axis from the status dot (``dotClass``), which the single
    // agent-status derivation owns — this never touches that dot.
    isReviewLoopRunning(session.review_loop) && 'has-review-loop',
    // ``kato:auto-wait``: kato does the work and its review, then waits for
    // the operator's Done — the row says so with a green border.
    session.automation === AUTOMATION_LEVEL.AUTO_WAIT && 'is-auto-wait',
  );

  const model = buildTaskTooltipModel(session, baseStatus, needsAttention, agent);

  // Where the press that could become a drag started.
  //
  // Every control on the row (pencil, pin, ×) is a plain
  // child of a draggable <li>, and the HTML drag model picks the nearest
  // ancestor with ``draggable=true`` — a button is not a barrier, and
  // ``draggable={false}`` on the child is NOT one either: the ancestor walk
  // looks only for ``true``, there is no stop-on-false. React's
  // ``stopPropagation`` does not help either; it stops the synthetic event,
  // not the browser's native drag.
  //
  // So the press is recorded, and the drag is CANCELLED at dragstart. Left
  // unguarded, pressing × and drifting a few pixels starts a reorder instead
  // of forgetting the task — and because a drag suppresses the click, the ×
  // does nothing at all.
  const pressedControlRef = useRef(false);

  function handleMouseDownCapture(event) {
    pressedControlRef.current = !!event?.target?.closest?.(CONTROL_SELECTOR);
  }

  function handleDragStart(event) {
    if (pressedControlRef.current) {
      // Cancelling here is what actually stops it — ``preventDefault`` on
      // ``dragstart`` is the spec's own "no drag" signal.
      event.preventDefault();
      return;
    }
    // The hover card cannot dismiss itself mid-drag: mouse events are
    // suppressed for the duration of a drag, so ``onMouseLeave`` never
    // fires. Worse, the mousedown that begins the drag re-arms the open
    // timer, so any drag lasting longer than the hover delay pops the card.
    closeTooltip();
    // The id travels in the dataTransfer payload as well as in state: a drop
    // has to know what moved, and React state updates are async.
    event.dataTransfer.effectAllowed = 'move';
    try {
      event.dataTransfer.setData('text/plain', session.task_id);
    } catch (_err) { /* some browsers restrict setData; state still carries it */ }
    if (onDragStart) { onDragStart(session.task_id); }
  }
  function handleDragOver(event) {
    // Only a same-group drop is legal — pinned tabs are a block at the front
    // — so the cursor must say "no" over an illegal target rather than let
    // the operator finish a drag that will be refused.
    if (!canDrop) { return; }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    if (onDragOver) { onDragOver(session.task_id); }
  }
  function handleDrop(event) {
    if (!canDrop) { return; }
    event.preventDefault();
    if (onDrop) { onDrop(session.task_id); }
  }

  return (
    <>
      <li
        ref={liRef}
        // Not draggable WHILE RENAMING: the rename box is a text input, and
        // a draggable ancestor swallows the click-and-drag that selects text
        // inside it.
        draggable={!renaming}
        onMouseDownCapture={handleMouseDownCapture}
        onDragStart={handleDragStart}
        onDragEnd={onDragEnd}
        onDragOver={handleDragOver}
        onDragLeave={onDragLeave}
        onDrop={handleDrop}
        className={className}
        // Lets the strip find THIS pill in the DOM to scroll it into
        // view when the operator selects the task from somewhere else
        // (the go-to-task palette, a notification). Cheaper and less
        // fragile than threading a ref down for one caller.
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
        <span
          ref={labelRef}
          className={cx('tab-label', renaming && 'is-renaming')}
        >
          <strong className="tab-label-id">{session.task_id}</strong>
          {parentTaskId && !nested ? (
            <span className="tab-label-subtask" role="img" aria-label={`Subtask of ${parentTaskId}`}>
              <Icon name="subtask" />
            </span>
          ) : null}
          {renaming ? (
            <input
              className="tab-label-rename"
              // Exactly the space the label already held — see renameBoxWidth.
              style={renameBoxWidth ? { width: `${renameBoxWidth}px` } : undefined}
              defaultValue={label}
              autoFocus
              maxLength={MAX_TAB_NAME_LENGTH}
              // Distinct from the pencil button's own label — two controls
              // sharing one accessible name is ambiguous to a screen reader.
              aria-label={`New name for the ${session.task_id} tab`}
              // Select the whole name on focus. autoFocus alone parks the
              // caret at the END of text that is mostly scrolled out of
              // sight, so retyping the name APPENDED to what was already
              // there — that is how a tab ends up labelled
              // "Core-lib skillCore-lib skillCore-lib skill". Select-all
              // makes the first keystroke replace, which is also the
              // standard rename gesture (VS Code, Finder).
              onFocus={(e) => e.target.select()}
              // The tab itself selects-on-click; without this, clicking
              // into your own text box switches tasks under you.
              onClick={(e) => e.stopPropagation()}
              onMouseDown={(e) => e.stopPropagation()}
              onBlur={(e) => commitRename(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { commitRename(e.target.value); }
                // Escape restores the previous label — including an empty
                // one, so a mis-typed rename is always undoable.
                if (e.key === 'Escape') { endRename(); }
                e.stopPropagation();
              }}
            />
          ) : label ? (
            <span
              className="tab-label-title"
              // Double-click still works for anyone who reaches for it, but
              // it is no longer the ONLY way in — a gesture with no visible
              // affordance is a guess. The pencil button below is the
              // discoverable path. No ``title`` here any more: a native
              // tooltip firing on the label text got in the way of simply
              // reading the strip.
              onDoubleClick={(e) => {
                if (typeof onRename !== 'function') { return; }
                e.stopPropagation();
                startRename();
              }}
            >
              {label}
            </span>
          ) : null}
        </span>
        {subtaskCount > 0 && typeof onToggleSubtasks === 'function' ? (
          <button
            type="button"
            className={cx(
              'tab-subtasks-toggle',
              !subtasksShown && 'is-folded',
              subtasksWaiting > 0 && 'has-attention',
            )}
            aria-expanded={subtasksShown ? 'true' : 'false'}
            aria-controls={subtasksShown ? subtaskListId(session.task_id) : undefined}
            aria-label={subtasksToggleLabel}
            title={subtasksToggleLabel}
            onClick={handleToggleSubtasks}
          >
            <Icon name="chevron-down" />
            <span className="tab-subtasks-count">{subtaskCount}</span>
          </button>
        ) : null}
        {/* Done or not, nothing to click: the toolbar's buttons open the
            task's review loop and its split plan. From the tab list's own
            data — no fetch per row. */}
        <ReviewLoopTabBadge loop={session.review_loop || null} />
        <PlanQueueTabBadge plan={session.plan_queue || null} finished={!!session.plan_queue_finished} />
        {typeof onRename === 'function' && !renaming && (
          <button
            type="button"
            className="tab-rename-btn"
            aria-label={`Rename the ${session.task_id} tab`}
            title="Rename tab"
            onClick={handleRename}
          >
            <Icon name="edit" />
          </button>
        )}
        <button
          type="button"
          className={cx('tab-pin-btn', pinned && 'is-pinned')}
          aria-label={pinned ? 'Unpin this task' : 'Pin this task'}
          aria-pressed={pinned}
          title={pinned ? 'Unpin task' : 'Pin task to the top'}
          onClick={handleTogglePin}
        >
          <Icon name="pin" />
        </button>
        <button
          type="button"
          className="tab-forget-btn"
          aria-label="Forget this task"
          // Records the intent at press time, before any retargeting can
          // happen. Cleared by whichever handler consumes it.
          onPointerDown={() => { pressedForgetRef.current = true; }}
          onClick={handleForget}
        >
          <Icon name="xmark" />
        </button>
      </li>
      {anchorRect && (
        <TabTooltip anchorRect={anchorRect} model={model} />
      )}
    </>
  );
}


// Each tab redraws only when its own props change. Without this, anything that
// touched ``App`` redrew every tab; ``reuseUnchanged`` and the stable handlers
// in ``TabList`` are what give this something to compare.
export default memo(Tab);
