import {
  useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { canDropOn, moveTab } from '../utils/tabOrder.js';
import { cx } from '../utils/cx.js';
import { groupSubtaskTabs } from '../utils/subtaskTabs.js';
import Icon, { BusyIcon } from './Icon.jsx';
import Tab from './Tab.jsx';
import { useDelayedFlag } from '../hooks/useDelayedFlag.js';
import {
  closeTaskMenu, closeTaskMenuSoon, openTaskMenu, useTaskMenuOpen,
} from '../stores/taskMenuStore.js';
import {
  orderByPinned,
  readPinnedIds,
  readTabOrder,
  writeTabOrder,
  togglePinned,
  writePinnedIds,
} from '../utils/pinnedTabs.js';
import {
  readTabNames,
  setTabName,
  tabNameFor,
  writeTabNames,
} from '../utils/taskTabNames.js';

/**
 * The tasks menu: every task as a row in a vertical list on the left edge,
 * like Chrome's vertical tabs. It opens while the pointer is over the kato
 * icon in the header or over the menu itself, and closes shortly after it
 * leaves both (``taskMenuStore``). Tab / Ctrl+Tab still walk the tasks with
 * the menu closed — the order is reported up to App.
 *
 * Each task is a row (``<li class="tab">``); a split plan's sub-tasks are rows
 * grouped under their main task. The actions (go to task, new task, add task,
 * delete tasks, scan now) head the menu. With no task at all the menu stays
 * open: the first-run "Create your first task" must not hide behind a hover.
 */
export default function TabList({
  sessions,
  // Has a session list ever arrived, and did the last poll reach kato? An
  // empty ``sessions`` alone cannot tell "you have no tasks" from "kato has
  // not answered yet" — and rendering the onboarding copy for the second
  // case reads as data loss. See hooks/useSessions.js.
  sessionsLoaded = true,
  katoReachable = true,
  activeTaskId,
  attentionTaskIds,
  agentStatuses = {},
  // Bumped ONLY when the operator picked a task from somewhere other than
  // the strip itself (the go-to-task palette). Scrolling the strip is a
  // response to THAT, and nothing else — see the effect below.
  revealRequestId = 0,
  onSelect,
  onForget,
  onOpenAddTask,
  onNewTask,
  onOpenBulkForget,
  onOpenTaskPalette,
  onScanNow,
  scanPending,
  onVisibleOrderChange,
  // The connected ticket system's name ('' = none: tasks are made in kato).
  trackerName = '',
}) {
  const menuOpen = useTaskMenuOpen();
  // Pinned tab ids (in pin order). localStorage-backed so the
  // operator's preference survives reloads. Read lazily on mount;
  // every toggle re-persists. See utils/pinnedTabs.js for the rules.
  const [pinnedIds, setPinnedIds] = useState(() => readPinnedIds());
  // Operator's local tab renames — same shape and lifecycle as pins.
  const [tabNames, setTabNames] = useState(() => readTabNames());
  // The operator's own left-to-right arrangement of the UNPINNED tabs.
  // Pinned order needs no separate store — it IS ``pinnedIds``.
  const [tabOrder, setTabOrder] = useState(() => readTabOrder());
  // Drag-to-reorder state, exactly as the file-tab strip keeps it: the tab
  // being dragged and the one it is over, together in one object so a single
  // setState can never leave a stale highlight behind after a drop.
  const [drag, setDrag] = useState({ id: '', over: '' });

  const handleRename = useCallback((taskId, label) => {
    setTabNames((prev) => {
      const next = setTabName(prev, taskId, label);
      writeTabNames(next);
      return next;
    });
  }, []);
  const handleTogglePin = useCallback((taskId) => {
    const pinning = !pinnedIds.includes(taskId);
    setPinnedIds((prev) => {
      const next = togglePinned(taskId, prev);
      writePinnedIds(next);
      return next;
    });
    // Pinning MOVES the row to the pinned block at the top; follow it, or on
    // a scrolled menu the row just clicked disappears upward. Unpinning stays
    // put: the operator usually clears several pins in a row.
    if (!pinning || typeof document === 'undefined') { return; }
    const follow = () => {
      const node = document.querySelector(
        `#tabs-pane [data-task-id="${CSS.escape(taskId)}"]`,
      );
      // jsdom (and a couple of older embedded browsers) have no
      // ``scrollIntoView``; not scrolling is cosmetic, so never throw.
      if (node && typeof node.scrollIntoView === 'function') {
        node.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
    };
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(follow);
    } else {
      follow();
    }
  }, [pinnedIds]);
  // ``orderByPinned`` is a pure sort; memoise on the inputs so we
  // don't reshuffle the list on unrelated re-renders.
  const orderedSessions = useMemo(
    () => orderByPinned(sessions, pinnedIds, tabOrder),
    [sessions, pinnedIds, tabOrder],
  );
  // A split plan's sub-tasks are drawn under their main task (a pinned one
  // keeps its place among the pins) — see utils/subtaskTabs.js.
  const { top: topSessions, childrenOf, order: tabCycleOrder } = useMemo(
    () => groupSubtaskTabs(orderedSessions, {
      keep: (session) => pinnedIds.includes(session.task_id),
    }),
    [orderedSessions, pinnedIds],
  );
  // The row a task is drawn in: '' = the outer level, else its main task.
  // A drag reorders among one level only (see ``canDrop`` below).
  const levelOf = useCallback((taskId) => {
    for (const [mainId, children] of childrenOf) {
      if (children.some((child) => child.task_id === taskId)) { return mainId; }
    }
    return '';
  }, [childrenOf]);

  // The menu's VISIBLE order, reported upward — each task with its
  // sub-tasks right after it (``tabCycleOrder``).
  //
  // Tab / Shift+Tab cycles tasks from App, which only had the raw
  // ``sessions`` array — so after a drag the keyboard still walked the
  // ORIGINAL order while the eye followed the new one: "I dragged a task
  // from last to be second, but when clicking Tab he is moving to the tab
  // in the old order".
  //
  // Reported rather than recomputed in App: the pinned ids and the drag
  // order are THIS component's state, and a second copy of the sort would
  // be a second thing to keep in step. Keyed on the joined ids so the
  // effect fires only when the order genuinely changes.
  const visibleOrderKey = tabCycleOrder.map((s) => s.task_id).join('\n');
  useEffect(() => {
    if (typeof onVisibleOrderChange === 'function') {
      onVisibleOrderChange(visibleOrderKey ? visibleOrderKey.split('\n') : []);
    }
  }, [visibleOrderKey, onVisibleOrderChange]);
  const pinnedSet = useMemo(() => new Set(pinnedIds), [pinnedIds]);

  // Drop ``fromId`` at ``toId``'s position.
  //
  // ``moveTab`` — the same primitive the file-tab strip uses — decides what
  // is legal, so both strips obey one rule: a tab may only be dropped among
  // its own group, and a cross-group drop is REFUSED rather than silently
  // relocated. A pinned tab dragged among the pins therefore stays pinned,
  // and an unpinned one can never take a pinned tab's place.
  //
  // Which list gets written depends on which group moved: pin order IS
  // ``pinnedIds``, while the unpinned arrangement needs its own record or
  // the next sessions poll would undo it.
  const handleReorder = useCallback((fromId, toId) => {
    const ordered = orderByPinned(sessions, pinnedIds, tabOrder);
    const next = moveTab(ordered, fromId, toId, {
      keyOf: (session) => session.task_id,
      pinnedOf: (session) => pinnedSet.has(session.task_id),
    });
    if (next === ordered) { return; }   // refused — leave everything alone
    const ids = next.map((session) => session.task_id);
    if (pinnedSet.has(fromId)) {
      const nextPinned = ids.filter((id) => pinnedSet.has(id));
      setPinnedIds(nextPinned);
      writePinnedIds(nextPinned);
      return;
    }
    const nextOrder = ids.filter((id) => !pinnedSet.has(id));
    setTabOrder(nextOrder);
    writeTabOrder(nextOrder);
  }, [sessions, pinnedIds, pinnedSet, tabOrder]);

  // Every handler a tab receives keeps its identity, or ``memo(Tab)`` is
  // defeated: a new function each render is a changed prop on every tab. What
  // they need that does change is read through refs at the moment of use.
  const dragRef = useRef(drag);
  dragRef.current = drag;
  const reorderRef = useRef(handleReorder);
  reorderRef.current = handleReorder;
  const handleDragStart = useCallback((id) => setDrag({ id, over: '' }), []);
  const handleDragEnd = useCallback(() => setDrag({ id: '', over: '' }), []);
  const handleDragOver = useCallback((id) => {
    setDrag((prev) => (prev.over === id ? prev : { ...prev, over: id }));
  }, []);
  const handleDragLeave = useCallback(() => {
    setDrag((prev) => (prev.over ? { ...prev, over: '' } : prev));
  }, []);
  const handleDrop = useCallback((id) => {
    const from = dragRef.current.id;
    if (from && from !== id) { reorderRef.current(from, id); }
    setDrag({ id: '', over: '' });
  }, []);

  // The menu also closes on a press outside it (a touch screen has no mouse
  // to leave with) and on Escape. Only while it is open.
  useEffect(() => {
    if (!menuOpen) { return undefined; }
    function onPress(event) {
      const target = event.target;
      if (target?.closest?.('#tabs-pane, #kato-menu-trigger')) { return; }
      closeTaskMenu();
    }
    function onKey(event) {
      if (event.key === 'Escape') { closeTaskMenu(); }
    }
    window.addEventListener('mousedown', onPress);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onPress);
      window.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  const draggedSession = drag.id
    ? orderedSessions.find((s) => s.task_id === drag.id) || null
    : null;

  // One row. A sub-task's row is a full row too (rename, pin, forget, drag
  // among its siblings): pinning one moves it up to the pins.
  const renderTab = (session, nested = false) => {
    const isActive = session.task_id === activeTaskId;
    const needsAttention = !!attentionTaskIds && attentionTaskIds.has(session.task_id);
    const liveStatus = agentStatuses[session.task_id] || null;
    return (
      <Tab
        key={session.task_id}
        session={session}
        active={isActive}
        needsAttention={needsAttention}
        liveStatus={liveStatus}
        pinned={pinnedSet.has(session.task_id)}
        onSelect={onSelect}
        onForget={onForget}
        onTogglePin={handleTogglePin}
        displayName={tabNameFor(tabNames, session.task_id, session.task_summary)}
        onRename={handleRename}
        nested={nested}
        dragging={drag.id === session.task_id}
        dropTarget={drag.over === session.task_id && drag.id !== session.task_id}
        // Among its own level only: a sub-task dropped among the main tasks
        // (or into another task's group) would move somewhere the eye cannot
        // follow.
        canDrop={levelOf(drag.id) === levelOf(session.task_id) && canDropOn(
          draggedSession, session,
          (s) => pinnedSet.has(s.task_id),
        )}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      />
    );
  };
  const rows = topSessions.flatMap((session) => {
    const children = childrenOf.get(session.task_id) || [];
    if (!children.length) { return [renderTab(session)]; }
    return [
      renderTab(session),
      <li key={`${session.task_id}::children`} className="tab-children" role="none">
        <ul aria-label={`Sub-tasks of ${session.task_id}`}>
          {children.map((child) => renderTab(child, true))}
        </ul>
      </li>,
    ];
  });

  // Bring the ACTIVE task's row into view — but ONLY when the reveal id
  // changed. Deliberately NOT keyed on activeTaskId or on sessions: those
  // change on every status poll and whenever the operator clicks a row they
  // can already see, and scrolling then would yank the menu out from under
  // their cursor. A scroll is intent too, and it has to win.
  const lastRevealRef = useRef(revealRequestId);
  useEffect(() => {
    if (revealRequestId === lastRevealRef.current) { return; }
    lastRevealRef.current = revealRequestId;
    if (!activeTaskId || typeof document === 'undefined') { return; }
    const node = document.querySelector(
      `#tabs-pane [data-task-id="${CSS.escape(activeTaskId)}"]`,
    );
    // Guarded: jsdom has no scrollIntoView, and neither do a couple of
    // older embedded browsers. Not being able to scroll is cosmetic — it
    // must never take the strip down with it.
    if (typeof node?.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    }
  }, [revealRequestId, activeTaskId]);

  // What an EMPTY strip actually means. Three different situations used to
  // share the onboarding sentence, and the two that are not "you have no
  // tasks" are the ones that scare people: a reload during kato's ~24s
  // startup, or a kato that went away, both said "No tabs yet. Click
  // + Add task" as if the workspaces had been wiped.
  //
  // The waiting copy is gated behind ``useDelayedFlag`` so the usual
  // millisecond load never flashes it — the strip just stays quiet until
  // either the tabs or a real answer arrive.
  const awaitingFirstLoad = !sessionsLoaded && katoReachable;
  const showWaiting = useDelayedFlag(awaitingFirstLoad, 600);
  let emptyStateMessage;
  if (!katoReachable) {
    emptyStateMessage = (
      <>
        Can’t reach kato right now — it may be starting up or stopped.
        Your tasks and workspaces are untouched; this list fills in as
        soon as kato answers.
      </>
    );
  } else if (awaitingFirstLoad) {
    emptyStateMessage = showWaiting ? <>Loading your tasks…</> : null;
  } else {
    // A new user's first screen: say how to get a first task, the way this
    // kato can — a ticket system is named only when one is connected.
    emptyStateMessage = (
      <>
        No tasks yet.{' '}
        <button type="button" className="tabs-empty-action" onClick={onNewTask}
          disabled={typeof onNewTask !== 'function'}>
          Create your first task
        </button>
        {' '}— describe the work and pick its repositories; kato plans it with you first.
        {trackerName ? (
          <> Or click <strong>+ Add task</strong> to pick one of your assigned {trackerName} tickets.</>
        ) : null}
      </>
    );
  }

  // The actions head the menu, above the task rows.
  const trailingActions = (
    <div className="tabs-actions">
      {/* Go to task. The Ctrl/Cmd+Shift+P shortcut is the fast path, but a
          shortcut nobody is told about does not exist — with a strip full
          of tasks (most scrolled out of sight) the only discoverable way
          to reach one was to scroll and read every pill. The tooltip
          names the shortcut so the button teaches it. */}
      <button
        type="button"
        id="tabs-go-to-task"
        className="tabs-action"
        data-tooltip="Go to task (Ctrl+Shift+P) — search your open tasks by id or name and jump straight to one."
        aria-label="Go to task"
        onClick={onOpenTaskPalette}
        disabled={typeof onOpenTaskPalette !== 'function'}
      >
        <Icon name="search" />
      </button>
      <button
        type="button"
        id="tabs-new-task"
        className="tabs-action"
        data-tooltip="New task — write one here in kato, no tracker: title, description, repositories. Starts in Plan."
        aria-label="New task"
        onClick={onNewTask}
        disabled={typeof onNewTask !== 'function'}
      >
        <Icon name="edit" />
      </button>
      <button
        type="button"
        id="tabs-add-task"
        className="tabs-action"
        data-tooltip="Add a task — pick from every task assigned to kato (open, in progress, in review, done) and provision its workspace."
        aria-label="Add a task"
        onClick={onOpenAddTask}
      >
        <Icon name="plus" />
      </button>
      {typeof onOpenBulkForget === 'function' && (
        <button
          type="button"
          className="tabs-action"
          data-tooltip="Delete tasks — pick several and remove their workspaces in one go."
          aria-label="Delete tasks"
          onClick={onOpenBulkForget}
        >
          <Icon name="trash" />
        </button>
      )}
      <button
        type="button"
        id="tabs-scan-now"
        className="tabs-action"
        data-tooltip={scanPending ? 'Scanning…' : 'Scan now — skip the idle wait and check for new tasks and review comments immediately.'}
        aria-label="Scan now"
        onClick={onScanNow}
        disabled={scanPending || !onScanNow}
      >
        <BusyIcon busy={scanPending} idle="refresh" />
      </button>
    </div>
  );

  const empty = rows.length === 0;
  // Held open only once there is something to say: the first split second of
  // a load says nothing yet (``useDelayedFlag``), and an empty panel must not
  // flash open over the screen.
  const heldOpen = empty && emptyStateMessage !== null;
  return (
    <nav
      id="tabs-pane"
      aria-label="Tasks"
      className={cx('tabs-pane-side', (menuOpen || heldOpen) && 'is-open', empty && 'is-empty')}
      onMouseEnter={openTaskMenu}
      onMouseLeave={closeTaskMenuSoon}
    >
      {trailingActions}
      {empty ? (
        <p id="empty-state" className="empty">
          {emptyStateMessage}
        </p>
      ) : (
        <ul id="tab-list">
          {rows}
        </ul>
      )}
    </nav>
  );
}
