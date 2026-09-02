import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { canDropOn, moveTab } from '../utils/tabOrder.js';
import Icon, { BusyIcon } from './Icon.jsx';
import Tab from './Tab.jsx';
import { useHorizontalWheelScroll } from '../hooks/useHorizontalWheelScroll.js';
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
 * iOS-style segmented controller at the top of the app.
 *
 * Each task gets a segment (rendered as `<li class="tab">` to keep
 * existing role-/structure-based tests green). The list scrolls
 * horizontally when it overflows the viewport; left/right chevron
 * buttons appear automatically on either side of the strip when
 * scrolling is possible, mirroring the iOS pattern.
 *
 * The two action buttons that lived in the old left-pane header
 * (Add task, Scan now) move to the trailing edge of the strip so
 * they're always reachable without scrolling.
 */
export default function TabList({
  sessions,
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
  onOpenTaskPalette,
  onScanNow,
  scanPending,
}) {
  const scrollRef = useRef(null);
  const [scrollState, setScrollState] = useState({
    canScrollLeft: false,
    canScrollRight: false,
  });
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

  function handleRename(taskId, label) {
    setTabNames((prev) => {
      const next = setTabName(prev, taskId, label);
      writeTabNames(next);
      return next;
    });
  }
  const handleTogglePin = useCallback((taskId) => {
    setPinnedIds((prev) => {
      const next = togglePinned(taskId, prev);
      writePinnedIds(next);
      return next;
    });
    // Follow the tab to its new home.
    //
    // Pinning MOVES the pill to the pinned block at the front, and the strip
    // scrolls horizontally — so on a right-scrolled strip the tab the
    // operator just clicked disappears leftward. Sticky positioning used to
    // hide this (a pinned tab was clamped into the scrollport by definition);
    // removing it made the jump visible, so the scroll has to be explicit
    // now. The pin click deliberately never reaches ``onSelect``, so nothing
    // else brings it back.
    if (typeof document === 'undefined') { return; }
    // After the re-render that re-sorts the strip, or we would scroll to
    // where the tab used to be.
    const follow = () => {
      const node = document.querySelector(
        `#tabs-pane [data-task-id="${CSS.escape(taskId)}"]`,
      );
      // jsdom (and a couple of older embedded browsers) have no
      // ``scrollIntoView``; not scrolling is cosmetic, so never throw.
      if (node && typeof node.scrollIntoView === 'function') {
        node.scrollIntoView({ inline: 'nearest', block: 'nearest' });
      }
    };
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(follow);
    } else {
      follow();
    }
  }, []);
  // ``orderByPinned`` is a pure sort; memoise on the inputs so we
  // don't reshuffle the list on unrelated re-renders.
  const orderedSessions = useMemo(
    () => orderByPinned(sessions, pinnedIds, tabOrder),
    [sessions, pinnedIds, tabOrder],
  );
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

  // Recompute "can I scroll?" any time the scroller's size or
  // content changes — that drives whether the chevron nav buttons
  // are visible. Without this, the chevrons would only update on
  // user scroll, leaving them visually stale after a tab is added
  // or removed.
  const recomputeScrollState = useCallback(() => {
    const node = scrollRef.current;
    if (!node) { return; }
    const canScrollLeft = node.scrollLeft > 2;
    const canScrollRight =
      node.scrollLeft + node.clientWidth < node.scrollWidth - 2;
    setScrollState((prev) =>
      prev.canScrollLeft === canScrollLeft
        && prev.canScrollRight === canScrollRight
        ? prev
        : { canScrollLeft, canScrollRight },
    );
  }, []);

  useEffect(() => {
    recomputeScrollState();
  }, [sessions, recomputeScrollState]);

  // Scroll/resize listeners that keep the chevron enabled-state live. These
  // MUST attach when the ``.tabs-scroller`` node attaches — a plain
  // ``useEffect`` reading ``scrollRef.current`` fires on the FIRST render,
  // when the empty-state branch means the strip isn't mounted yet, and never
  // re-runs (its deps don't change), so the listeners never attached after
  // sessions loaded and the chevrons only refreshed on the next poll. Riding
  // the wheel hook's callback ref (same node, same attach/detach lifecycle)
  // fixes that with no second ref competing for the one ref slot.
  const attachScrollTracking = useCallback((node) => {
    const onScroll = () => recomputeScrollState();
    const onResize = () => recomputeScrollState();
    node.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onResize);
    recomputeScrollState();  // initial compute now that the node exists
    return () => {
      node.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onResize);
    };
  }, [recomputeScrollState]);
  // Vertical-mouse-wheel-to-horizontal-scroll remap — shared with
  // every other horizontally-scrolling tab strip (see the hook for
  // the Windows/Firefox deltaMode normalisation this fixes, and why
  // this is a CALLBACK ref rather than a plain useRef + useEffect —
  // the empty-state branch below means .tabs-scroller isn't mounted
  // on the very first render, which a plain useEffect would miss). The
  // scroll/resize tracking rides the same callback ref via onAttach.
  const wheelRef = useHorizontalWheelScroll(scrollRef, attachScrollTracking);

  // No auto-scroll-into-view on tab selection: the operator's own
  // scroll position is intentional and must not be fought. With many
  // open tasks, ``sessions`` changes on every status poll, and a
  // dependency on it here would re-center the active tab mid-scroll
  // (the reported bug) — better to let operators navigate freely and
  // use the chevrons or wheel to reach a tab that's off-screen.

  function scrollByPage(direction) {
    const node = scrollRef.current;
    if (!node) { return; }
    const delta = Math.round(node.clientWidth * 0.7) * (direction === 'next' ? 1 : -1);
    node.scrollBy({ left: delta, behavior: 'smooth' });
  }

  // ----- hold-to-scroll on the chevron buttons ------------------
  // Click ➜ one-page jump (above). Press-and-hold ➜ continuous
  // scroll until release. Implemented as a rAF loop so the
  // animation stays smooth on any frame rate; an interval timer
  // would tear under load. Touch + mouse both feed the same
  // start/stop pair.
  const holdRef = useRef({ rafId: 0, direction: 0 });

  const stopHoldScroll = useCallback(() => {
    if (holdRef.current.rafId) {
      cancelAnimationFrame(holdRef.current.rafId);
      holdRef.current.rafId = 0;
    }
    holdRef.current.direction = 0;
  }, []);

  const startHoldScroll = useCallback((direction) => {
    const node = scrollRef.current;
    if (!node) { return; }
    stopHoldScroll();
    holdRef.current.direction = direction === 'next' ? 1 : -1;
    // ~8 px per frame ≈ 480 px/s at 60Hz — fast enough to step
    // through a long task list in a couple seconds, slow enough
    // that the operator can release mid-scroll on the target tab.
    const STEP_PX = 8;
    const tick = () => {
      const dir = holdRef.current.direction;
      if (!dir) { return; }
      node.scrollLeft += dir * STEP_PX;
      // Stop automatically when we hit the edge so the operator
      // doesn't have to keep holding past the end.
      const atEdge = dir > 0
        ? node.scrollLeft + node.clientWidth >= node.scrollWidth - 1
        : node.scrollLeft <= 0;
      if (atEdge) {
        stopHoldScroll();
        return;
      }
      holdRef.current.rafId = requestAnimationFrame(tick);
    };
    holdRef.current.rafId = requestAnimationFrame(tick);
  }, [stopHoldScroll]);

  // Global mouseup / touchend listeners — a button's own onMouseUp
  // misses the release when the operator drags off the button
  // before letting go, which would leave the scroll loop running.
  useEffect(() => {
    function onUp() { stopHoldScroll(); }
    window.addEventListener('mouseup', onUp);
    window.addEventListener('touchend', onUp);
    window.addEventListener('touchcancel', onUp);
    window.addEventListener('blur', onUp);
    return () => {
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('touchend', onUp);
      window.removeEventListener('touchcancel', onUp);
      window.removeEventListener('blur', onUp);
      stopHoldScroll();
    };
  }, [stopHoldScroll]);

  function bindHold(direction) {
    return {
      onMouseDown: () => startHoldScroll(direction),
      onTouchStart: () => startHoldScroll(direction),
      onMouseLeave: () => stopHoldScroll(),
    };
  }

  const draggedSession = drag.id
    ? orderedSessions.find((s) => s.task_id === drag.id) || null
    : null;

  const tabs = orderedSessions.map((session) => {
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
        dragging={drag.id === session.task_id}
        dropTarget={drag.over === session.task_id && drag.id !== session.task_id}
        canDrop={canDropOn(
          draggedSession, session,
          (s) => pinnedSet.has(s.task_id),
        )}
        onDragStart={(id) => setDrag({ id, over: '' })}
        onDragEnd={() => setDrag({ id: '', over: '' })}
        onDragOver={(id) => setDrag((prev) => (
          prev.over === id ? prev : { ...prev, over: id }
        ))}
        onDragLeave={() => setDrag((prev) => (
          prev.over ? { ...prev, over: '' } : prev
        ))}
        onDrop={(id) => {
          if (drag.id && drag.id !== id) { handleReorder(drag.id, id); }
          setDrag({ id: '', over: '' });
        }}
      />
    );
  });

  // NOTE: pinned tabs used to be ``position: sticky``, held against the left
  // edge while the rest of the strip scrolled underneath, with a layout
  // effect here measuring each one and publishing a ``--sticky-left`` offset
  // (plus a ResizeObserver to keep those offsets fresh).
  //
  // It could not work once the pinned cluster grew wider than the strip:
  // there is simply nowhere left to hold them, so they piled up and painted
  // over each other. Two separate fixes went into the measuring — descending
  // z-index, fractional widths — and neither touched that, because the
  // problem was the premise rather than the arithmetic.
  //
  // Pinned tabs now just SORT to the front (``orderByPinned`` above) and
  // scroll like every other tab. The whole mechanism is gone rather than
  // repaired: no offsets to publish, no observer, and no way for two tabs to
  // occupy the same space.

  // Bring the ACTIVE task's tab into view — but ONLY when the reveal id
  // changed. Deliberately NOT keyed on activeTaskId or on sessions: those
  // change on every status poll and whenever the operator clicks a tab
  // they can already see, and re-centring then would yank the strip back
  // under their cursor while they were reaching for a different tab. A
  // scroll is intent too, and it has to win. Same rule the file-tab strip
  // follows.
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

  // Trailing actions live in their own pill so they stay visually
  // separated from the segments (and don't get swallowed by the
  // horizontal scroll).
  const trailingActions = (
    <div className="tabs-actions">
      {/* Go to task. The Ctrl/Cmd+Shift+F shortcut is the fast path, but a
          shortcut nobody is told about does not exist — with a strip full
          of tasks (most scrolled out of sight) the only discoverable way
          to reach one was to scroll and read every pill. The tooltip
          names the shortcut so the button teaches it. */}
      <button
        type="button"
        id="tabs-go-to-task"
        className="tabs-action"
        data-tooltip="Go to task (Ctrl+Shift+F) — search your open tasks by id or name and jump straight to one."
        aria-label="Go to task"
        onClick={onOpenTaskPalette}
        disabled={typeof onOpenTaskPalette !== 'function'}
      >
        <Icon name="search" />
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

  if (tabs.length === 0) {
    return (
      <nav id="tabs-pane" className="tabs-pane-top is-empty">
        <p id="empty-state" className="empty">
          No tabs yet. Click <strong>+ Add task</strong> to pick one
          of your assigned tasks, or tag a YouTrack task with{' '}
          <code>kato:wait-planning</code> and let kato pick it up
          autonomously.
        </p>
        {trailingActions}
      </nav>
    );
  }
  return (
    <nav id="tabs-pane" className="tabs-pane-top">
      <button
        type="button"
        className="tabs-nav-button tabs-nav-prev"
        data-tooltip="Scroll tabs left (click) or hold to keep scrolling"
        aria-label="Scroll tabs left"
        onClick={() => scrollByPage('prev')}
        disabled={!scrollState.canScrollLeft}
        {...bindHold('prev')}
      >
        <Icon name="chevron-left" />
      </button>
      <div className="tabs-scroller" ref={wheelRef}>
        <ul id="tab-list">
          {tabs}
        </ul>
      </div>
      <button
        type="button"
        className="tabs-nav-button tabs-nav-next"
        data-tooltip="Scroll tabs right (click) or hold to keep scrolling"
        aria-label="Scroll tabs right"
        onClick={() => scrollByPage('next')}
        disabled={!scrollState.canScrollRight}
        {...bindHold('next')}
      >
        <Icon name="chevron-right" />
      </button>
      {trailingActions}
    </nav>
  );
}
