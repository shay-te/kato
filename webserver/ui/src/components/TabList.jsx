import { useCallback, useEffect, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import Tab from './Tab.jsx';

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
  onSelect,
  onForget,
  onOpenAddTask,
  onScanNow,
  scanPending,
}) {
  const scrollRef = useRef(null);
  const [scrollState, setScrollState] = useState({
    canScrollLeft: false,
    canScrollRight: false,
  });

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

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) { return undefined; }
    const onScroll = () => recomputeScrollState();
    const onResize = () => recomputeScrollState();
    // Mouse wheels on non-Mac platforms emit deltaY only by
    // default; in a horizontal strip that would scroll the page
    // instead of the segments. We map any "mostly vertical"
    // wheel event to a horizontal scroll on this node so a
    // single-axis wheel can step through tabs. Trackpad gestures
    // (which already carry deltaX) are passed through untouched.
    const onWheel = (event) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) {
        return;
      }
      // Only consume the event when there's actually room to
      // scroll in that direction — otherwise the page should
      // still scroll normally.
      const goingRight = event.deltaY > 0;
      const atEnd = goingRight
        ? node.scrollLeft + node.clientWidth >= node.scrollWidth - 1
        : node.scrollLeft <= 0;
      if (atEnd) { return; }
      event.preventDefault();
      node.scrollLeft += event.deltaY;
    };
    node.addEventListener('scroll', onScroll, { passive: true });
    node.addEventListener('wheel', onWheel, { passive: false });
    window.addEventListener('resize', onResize);
    return () => {
      node.removeEventListener('scroll', onScroll);
      node.removeEventListener('wheel', onWheel);
      window.removeEventListener('resize', onResize);
    };
  }, [recomputeScrollState]);

  // When the active task changes, scroll its segment into view —
  // operators using the keyboard / external task tag flips
  // shouldn't have to find the new tab themselves.
  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !activeTaskId) { return; }
    const active = node.querySelector(`[data-task-id="${activeTaskId}"]`);
    if (active && typeof active.scrollIntoView === 'function') {
      active.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
    }
  }, [activeTaskId, sessions]);

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

  const tabs = (sessions || []).map((session) => {
    const isActive = session.task_id === activeTaskId;
    const needsAttention = !!attentionTaskIds && attentionTaskIds.has(session.task_id);
    return (
      <Tab
        key={session.task_id}
        session={session}
        active={isActive}
        needsAttention={needsAttention}
        onSelect={onSelect}
        onForget={onForget}
      />
    );
  });

  // Trailing actions live in their own pill so they stay visually
  // separated from the segments (and don't get swallowed by the
  // horizontal scroll).
  const trailingActions = (
    <div className="tabs-actions">
      <button
        type="button"
        id="tabs-add-task"
        className="tabs-action tooltip-below"
        data-tooltip="Add a task — pick from every task assigned to kato (open, in progress, in review, done) and provision its workspace."
        aria-label="Add a task"
        onClick={onOpenAddTask}
      >
        <Icon name="plus" />
      </button>
      <button
        type="button"
        id="tabs-scan-now"
        className="tabs-action tooltip-below"
        data-tooltip={scanPending ? 'Scanning…' : 'Scan now — skip the idle wait and check for new tasks and review comments immediately.'}
        aria-label="Scan now"
        onClick={onScanNow}
        disabled={scanPending || !onScanNow}
      >
        <Icon name={scanPending ? 'spinner' : 'refresh'} spin={scanPending} />
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
        className="tabs-nav-button tabs-nav-prev tooltip-below"
        data-tooltip="Scroll tabs left (click) or hold to keep scrolling"
        aria-label="Scroll tabs left"
        onClick={() => scrollByPage('prev')}
        disabled={!scrollState.canScrollLeft}
        {...bindHold('prev')}
      >
        <Icon name="chevron-left" />
      </button>
      <div className="tabs-scroller" ref={scrollRef}>
        <ul id="tab-list">
          {tabs}
        </ul>
      </div>
      <button
        type="button"
        className="tabs-nav-button tabs-nav-next tooltip-below"
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
