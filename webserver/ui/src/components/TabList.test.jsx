// Tests for TabList. Maps sessions to Tabs in a <ul> and renders
// the header buttons (Add task, Scan now). Empty state shows when
// the sessions list is empty.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import TabList from './TabList.jsx';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import {
  PINNED_TABS_STORAGE_KEY,
  readPinnedIds,
} from '../utils/pinnedTabs.js';


function _session(taskId, overrides = {}) {
  return {
    task_id: taskId,
    task_summary: `Summary ${taskId}`,
    status: TAB_STATUS.ACTIVE,
    working: false,
    live: true,
    [AGENT_SESSION_ID]: 'sess',
    ...overrides,
  };
}


describe('TabList', () => {

  test('renders each session as a Tab', () => {
    render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        activeTaskId="A-2"
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText('A-1')).toBeInTheDocument();
    expect(screen.getByText('A-2')).toBeInTheDocument();
    expect(screen.getByText('A-3')).toBeInTheDocument();
  });

  test('marks the activeTaskId tab as active', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        activeTaskId="A-2"
        onSelect={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('li.tab');
    expect(tabs[0]).not.toHaveClass('active');
    expect(tabs[1]).toHaveClass('active');
  });

  test('uses agentStatuses by task id so a non-active working tab is orange', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1', { status: TAB_STATUS.REVIEW }), _session('A-2')]}
        activeTaskId="A-2"
        agentStatuses={{
          'A-1': { lifecycle: SESSION_LIFECYCLE.STREAMING, turnInFlight: true },
        }}
        onSelect={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('li.tab');
    expect(tabs[0].querySelector('.status-dot')).toHaveClass(`status-${TAB_STATUS.WORKING}`);
    expect(tabs[1].querySelector('.status-dot')).not.toHaveClass(`status-${TAB_STATUS.WORKING}`);
  });

  test('selecting/updating the active tab never auto-scrolls the strip', () => {
    // Regression: an effect that re-centered the active tab on every
    // ``sessions`` change (e.g. a status poll) fought the operator's
    // own manual scroll/wheel input, making the strip feel like it
    // "jumped back" mid-scroll. Selection must never move the strip.
    const scrollToSpy = vi.fn();
    const scrollIntoViewSpy = vi.fn();
    const origScrollTo = window.HTMLElement.prototype.scrollTo;
    const origSIV = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollTo = scrollToSpy;
    window.HTMLElement.prototype.scrollIntoView = scrollIntoViewSpy;
    try {
      const { rerender } = render(
        <TabList
          sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
          activeTaskId="A-3"
          onSelect={() => {}}
        />,
      );
      // Simulate a status poll producing a new sessions array while
      // the active tab stays the same.
      rerender(
        <TabList
          sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
          activeTaskId="A-3"
          onSelect={() => {}}
        />,
      );
      expect(scrollToSpy).not.toHaveBeenCalled();
      expect(scrollIntoViewSpy).not.toHaveBeenCalled();
    } finally {
      window.HTMLElement.prototype.scrollTo = origScrollTo;
      window.HTMLElement.prototype.scrollIntoView = origSIV;
    }
  });

  test('marks tabs in attentionTaskIds as needs-attention', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        attentionTaskIds={new Set(['A-1'])}
        onSelect={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('li.tab');
    expect(tabs[0]).toHaveClass('needs-attention');
    expect(tabs[1]).not.toHaveClass('needs-attention');
  });

  test('renders the empty-state copy when sessions is empty', () => {
    render(<TabList sessions={[]} onSelect={() => {}} />);
    expect(screen.getByText(/No tabs yet/)).toBeInTheDocument();
    // "+ Add task" appears as a strong inside the empty-state copy
    // (separate from the header button). Use the empty-state id to
    // disambiguate from the button's aria-label.
    expect(screen.getByText('+ Add task')).toBeInTheDocument();
  });

  test('renders the empty-state when sessions is undefined (defensive)', () => {
    render(<TabList sessions={undefined} onSelect={() => {}} />);
    expect(screen.getByText(/No tabs yet/)).toBeInTheDocument();
  });

  test('Add task button fires onOpenAddTask', () => {
    const onOpenAddTask = vi.fn();
    render(<TabList sessions={[]} onSelect={() => {}} onOpenAddTask={onOpenAddTask} />);
    fireEvent.click(screen.getByLabelText('Add a task'));
    expect(onOpenAddTask).toHaveBeenCalledTimes(1);
  });

  test('Scan now button fires onScanNow when enabled', () => {
    const onScanNow = vi.fn();
    render(
      <TabList sessions={[]} onSelect={() => {}} onScanNow={onScanNow} scanPending={false} />,
    );
    fireEvent.click(screen.getByLabelText('Scan now'));
    expect(onScanNow).toHaveBeenCalledTimes(1);
  });

  test('Scan now button is disabled while scanPending is true', () => {
    render(
      <TabList sessions={[]} onSelect={() => {}} onScanNow={() => {}} scanPending={true} />,
    );
    expect(screen.getByLabelText('Scan now')).toBeDisabled();
  });

  test('Scan now button is disabled when onScanNow is not provided', () => {
    render(<TabList sessions={[]} onSelect={() => {}} />);
    expect(screen.getByLabelText('Scan now')).toBeDisabled();
  });
});


describe('TabList — pinned tab ordering', () => {

  beforeEach(() => {
    // Each test owns its own pinned set; don't leak across cases.
    window.localStorage.removeItem(PINNED_TABS_STORAGE_KEY);
  });

  function tabOrder(container) {
    return Array.from(container.querySelectorAll('li.tab strong'))
      .map((el) => el.textContent);
  }

  test('with no pinned tabs the order matches the session input', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        onSelect={() => {}}
      />,
    );
    expect(tabOrder(container)).toEqual(['A-1', 'A-2', 'A-3']);
  });

  test('pinned tasks render at the left in pin order', () => {
    // Seed localStorage so the initial render already has the pins.
    window.localStorage.setItem(
      PINNED_TABS_STORAGE_KEY, JSON.stringify(['A-3', 'A-1']),
    );
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3'), _session('A-4')]}
        onSelect={() => {}}
      />,
    );
    expect(tabOrder(container)).toEqual(['A-3', 'A-1', 'A-2', 'A-4']);
  });

  test('clicking a tab\'s pin button persists + reorders without crashing', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        onSelect={() => {}}
      />,
    );
    // Pin A-3 → it should move to the leftmost slot.
    const pinButtons = container.querySelectorAll('.tab-pin-btn');
    fireEvent.click(pinButtons[2]);
    expect(tabOrder(container)).toEqual(['A-3', 'A-1', 'A-2']);
    // And persisted.
    expect(readPinnedIds(window.localStorage)).toEqual(['A-3']);
  });

  test('pin → unpin returns the tab to its original position', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        onSelect={() => {}}
      />,
    );
    const pinBtnsBefore = container.querySelectorAll('.tab-pin-btn');
    fireEvent.click(pinBtnsBefore[1]); // pin A-2
    expect(tabOrder(container)).toEqual(['A-2', 'A-1', 'A-3']);
    // The A-2 tab is now FIRST — its pin button is at index 0.
    const pinBtnsAfter = container.querySelectorAll('.tab-pin-btn');
    fireEvent.click(pinBtnsAfter[0]); // unpin A-2
    expect(tabOrder(container)).toEqual(['A-1', 'A-2', 'A-3']);
    expect(readPinnedIds(window.localStorage)).toEqual([]);
  });

  test('pinned tab gets is-pinned class so it sorts to the front of the strip', () => {
    window.localStorage.setItem(
      PINNED_TABS_STORAGE_KEY, JSON.stringify(['A-2']),
    );
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        onSelect={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('li.tab');
    // A-2 is pinned → moved to position 0 → marked .is-pinned.
    expect(tabs[0]).toHaveClass('is-pinned');
    expect(tabs[1]).not.toHaveClass('is-pinned');
  });

  test('stale pinned ids (no matching session) are silently ignored', () => {
    window.localStorage.setItem(
      PINNED_TABS_STORAGE_KEY,
      JSON.stringify(['T-deleted', 'A-2']),
    );
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        onSelect={() => {}}
      />,
    );
    // Stale 'T-deleted' is dropped; A-2 still pins to the left.
    expect(tabOrder(container)).toEqual(['A-2', 'A-1']);
  });

  test('a LINE-mode mouse wheel scrolls the strip by pixels, not by ~3px', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        onSelect={() => {}}
      />,
    );
    const scroller = container.querySelector('.tabs-scroller');
    // jsdom does no layout, so fake a strip that CAN scroll right.
    Object.defineProperty(scroller, 'clientWidth', { value: 100, configurable: true });
    Object.defineProperty(scroller, 'scrollWidth', { value: 500, configurable: true });
    scroller.scrollLeft = 0;

    // A physical wheel on Windows/Firefox reports deltaMode 1 (LINES):
    // deltaY of 3 means "3 lines". The old code did scrollLeft += 3 (dead).
    // Normalised, 3 lines → 48px.
    const wheel = new WheelEvent('wheel', {
      deltaY: 3, deltaMode: 1, bubbles: true, cancelable: true,
    });
    scroller.dispatchEvent(wheel);

    expect(scroller.scrollLeft).toBe(48);
    expect(wheel.defaultPrevented).toBe(true);
  });

  test('a mostly-horizontal (trackpad) wheel is left to the browser', () => {
    const { container } = render(
      <TabList sessions={[_session('A-1'), _session('A-2')]} onSelect={() => {}} />,
    );
    const scroller = container.querySelector('.tabs-scroller');
    Object.defineProperty(scroller, 'clientWidth', { value: 100, configurable: true });
    Object.defineProperty(scroller, 'scrollWidth', { value: 500, configurable: true });
    scroller.scrollLeft = 10;
    // deltaX dominates → the handler must NOT hijack it (native horizontal
    // trackpad scroll already works).
    const wheel = new WheelEvent('wheel', {
      deltaX: 40, deltaY: 5, bubbles: true, cancelable: true,
    });
    scroller.dispatchEvent(wheel);
    expect(scroller.scrollLeft).toBe(10);
    expect(wheel.defaultPrevented).toBe(false);
  });

  test('THE REGRESSION: wheel scroll still works after the FIRST session loads (starts on the empty-state placeholder)', () => {
    // The real-world sequence on every app load: sessions start as []
    // (before the initial /api/sessions fetch resolves), so TabList
    // renders the empty-state placeholder — no .tabs-scroller exists
    // yet. A plain useRef + useEffect hook only runs its attach-effect
    // once, on that first (no scroller) render, and never re-runs once
    // sessions actually populate and the real strip mounts — so the
    // wheel listener silently never attached. This is the exact
    // transition that must be exercised, not just "render with
    // sessions already present" (which happened to mask the bug).
    const { container, rerender } = render(
      <TabList sessions={[]} onSelect={() => {}} />,
    );
    expect(container.querySelector('.tabs-scroller')).toBeNull();

    rerender(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        onSelect={() => {}}
      />,
    );
    const scroller = container.querySelector('.tabs-scroller');
    Object.defineProperty(scroller, 'clientWidth', { value: 100, configurable: true });
    Object.defineProperty(scroller, 'scrollWidth', { value: 500, configurable: true });
    scroller.scrollLeft = 0;
    const wheel = new WheelEvent('wheel', {
      deltaY: 50, deltaX: 0, bubbles: true, cancelable: true,
    });
    scroller.dispatchEvent(wheel);
    expect(scroller.scrollLeft).toBe(50);
    expect(wheel.defaultPrevented).toBe(true);
  });
});

describe('TabList — pinned tabs scroll like every other tab', () => {
  // They used to be ``position: sticky`` against the left edge, with a layout
  // effect measuring each one and publishing a ``--sticky-left`` offset so
  // the cluster stacked horizontally instead of collapsing at left:0.
  //
  // That premise fails as soon as the pinned cluster is wider than the strip:
  // there is nowhere left to hold them, so they piled up and painted over one
  // another — reported as "the tasks got below eachother, they dont scroll
  // like normal tabs". Two rounds of fixes went into the measuring (z-index
  // order, fractional widths) and neither could address it.
  //
  // Pinned tabs are now ORDERED first and scroll normally. These tests pin
  // the absence of the mechanism, so it cannot creep back.

  beforeEach(() => {
    window.localStorage.removeItem(PINNED_TABS_STORAGE_KEY);
  });

  function renderPinned(pinnedIds) {
    window.localStorage.setItem(
      PINNED_TABS_STORAGE_KEY, JSON.stringify(pinnedIds),
    );
    return render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        activeTaskId="A-1"
        onSelect={() => {}}
      />,
    );
  }

  test('no per-tab sticky offset is written any more', () => {
    const { container } = renderPinned(['A-1', 'A-2', 'A-3']);
    const pinned = container.querySelectorAll('li.tab.is-pinned');
    expect(pinned).toHaveLength(3);
    for (const el of pinned) {
      expect(el.style.getPropertyValue('--sticky-left')).toBe('');
    }
  });

  test('no per-tab z-index is written any more', () => {
    // The stacking order only mattered while tabs could overlap.
    const { container } = renderPinned(['A-1', 'A-2']);
    for (const el of container.querySelectorAll('li.tab')) {
      expect(el.style.zIndex).toBe('');
    }
  });

  test('pinned tabs still come first, which is what pinning is for', () => {
    const { container } = renderPinned(['A-3']);
    const tabs = [...container.querySelectorAll('li.tab')];
    expect(tabs[0].dataset.taskId).toBe('A-3');
    expect(tabs[0].classList.contains('is-pinned')).toBe(true);
  });

  test('pinning another task re-sorts without touching layout offsets', () => {
    const { container } = renderPinned(['A-2']);
    fireEvent.click(container.querySelectorAll('.tab-pin-btn')[1]);
    const pinned = container.querySelectorAll('li.tab.is-pinned');
    expect(pinned).toHaveLength(2);
    for (const el of pinned) {
      expect(el.style.getPropertyValue('--sticky-left')).toBe('');
    }
  });
});

describe('TabList — go-to-task button', () => {
  function renderStrip(props = {}) {
    render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        activeTaskId="A-1"
        onSelect={() => {}}
        {...props}
      />,
    );
  }

  test('renders a search action that opens the task palette', () => {
    // The Ctrl+Shift+F shortcut is the fast path, but a shortcut nobody is told
    // about does not exist — with a full strip the only discoverable way
    // to reach a task was to scroll and read every pill.
    const onOpenTaskPalette = vi.fn();
    renderStrip({ onOpenTaskPalette });
    fireEvent.click(screen.getByRole('button', { name: /go to task/i }));
    expect(onOpenTaskPalette).toHaveBeenCalled();
  });

  test('the tooltip teaches the keyboard shortcut', () => {
    renderStrip({ onOpenTaskPalette: vi.fn() });
    const button = screen.getByRole('button', { name: /go to task/i });
    expect(button.getAttribute('data-tooltip')).toMatch(/Ctrl\+Shift\+F/);
  });

  test('it is disabled when no handler is supplied', () => {
    renderStrip({});
    expect(screen.getByRole('button', { name: /go to task/i })).toBeDisabled();
  });
});

describe('TabList — scroll the selected tab into view', () => {
  function renderStrip(props = {}) {
    return render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        activeTaskId="A-3"
        onSelect={() => {}}
        {...props}
      />,
    );
  }

  test('each tab carries its task id so the strip can find it', () => {
    renderStrip();
    expect(document.querySelector('[data-task-id="A-3"]')).toBeTruthy();
  });

  test('bumping the reveal id scrolls the ACTIVE tab into view', () => {
    // Picking a task from the palette must bring its pill into view —
    // otherwise the operator lands on a task still scrolled off-screen
    // with no visual confirmation of where they are.
    const { rerender } = renderStrip({ revealRequestId: 1 });
    const node = document.querySelector('[data-task-id="A-3"]');
    node.scrollIntoView = vi.fn();
    rerender(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        activeTaskId="A-3"
        onSelect={() => {}}
        revealRequestId={2}
      />,
    );
    expect(node.scrollIntoView).toHaveBeenCalled();
  });

  test('a re-render with the SAME reveal id does not move the strip', () => {
    // Sessions re-render on every status poll. Re-centring then would
    // yank the strip back under the operator's cursor while they were
    // reaching for a different tab — a scroll is intent too.
    const { rerender } = renderStrip({ revealRequestId: 1 });
    const node = document.querySelector('[data-task-id="A-3"]');
    node.scrollIntoView = vi.fn();
    rerender(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3'), _session('A-4')]}
        activeTaskId="A-3"
        onSelect={() => {}}
        revealRequestId={1}
      />,
    );
    expect(node.scrollIntoView).not.toHaveBeenCalled();
  });

  test('a missing scrollIntoView (jsdom, old webviews) does not break the strip', () => {
    const { rerender } = renderStrip({ revealRequestId: 1 });
    expect(() => rerender(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        activeTaskId="A-3"
        onSelect={() => {}}
        revealRequestId={2}
      />,
    )).not.toThrow();
  });
});

describe('TabList — drag to reorder task tabs', () => {
  // Same interaction the file-tab strip inside a task already had, and the
  // same rule: pinned tabs are a block at the front, a tab may only be
  // dropped among its own group, and a cross-group drop is REFUSED rather
  // than silently relocated.

  beforeEach(() => {
    window.localStorage.removeItem(PINNED_TABS_STORAGE_KEY);
    window.localStorage.removeItem('kato.tabs.order');
  });

  function renderStrip(pinnedIds = []) {
    if (pinnedIds.length) {
      window.localStorage.setItem(
        PINNED_TABS_STORAGE_KEY, JSON.stringify(pinnedIds),
      );
    }
    return render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        activeTaskId="A-1"
        onSelect={() => {}}
      />,
    );
  }

  function ids(container) {
    return [...container.querySelectorAll('li.tab')]
      .map((el) => el.dataset.taskId);
  }

  function dragOnto(container, fromId, toId) {
    const tabs = [...container.querySelectorAll('li.tab')];
    const from = tabs.find((el) => el.dataset.taskId === fromId);
    const to = tabs.find((el) => el.dataset.taskId === toId);
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData() {} };
    fireEvent.dragStart(from, { dataTransfer });
    fireEvent.dragOver(to, { dataTransfer });
    fireEvent.drop(to, { dataTransfer });
  }

  test('a tab is draggable', () => {
    const { container } = renderStrip();
    expect(container.querySelector('li.tab').draggable).toBe(true);
  });

  // Every control on the pill is a plain child of a draggable <li>, and the
  // drag model picks the nearest ancestor with draggable=true — a button is
  // not a barrier, and draggable={false} on the child is NOT one either (the
  // walk looks only for true). Unguarded, pressing × and drifting a few
  // pixels reorders the tab instead of forgetting it, and the drag suppresses
  // the click so the × does nothing at all.
  //
  // Asserted via defaultPrevented: cancelling dragstart is the spec's own
  // "no drag" signal, and it is the only thing that actually stops it. An
  // earlier version of this test asserted ``handle.draggable === false``,
  // which a <span> with no attribute reports anyway — it passed with the
  // guard deleted.
  for (const [name, selector] of [
    ['the resize handle', '.tab-resize-handle'],
    ['a control button', 'button'],
  ]) {
    test(`a drag starting on ${name} is cancelled`, () => {
      const { container } = renderStrip();
      const tab = container.querySelector('li.tab');
      const control = tab.querySelector(selector);
      expect(control).toBeTruthy();
      fireEvent.mouseDown(control);
      const started = fireEvent.dragStart(tab, {
        dataTransfer: { effectAllowed: '', setData() {} },
      });
      // fireEvent returns false when a handler called preventDefault.
      expect(started).toBe(false);
    });
  }

  test('a drag starting on the pill itself is NOT cancelled', () => {
    // The guard must not break the feature it protects.
    const { container } = renderStrip();
    const tab = container.querySelector('li.tab');
    fireEvent.mouseDown(tab);
    const started = fireEvent.dragStart(tab, {
      dataTransfer: { effectAllowed: '', setData() {} },
    });
    expect(started).toBe(true);
  });

  test('dragover over a legal target is cancelled, or no drop ever fires', () => {
    // ``drop`` only fires after a CANCELLED ``dragover``. Without the
    // preventDefault the whole feature is dead in every browser while every
    // jsdom test still passes, because jsdom dispatches ``drop`` regardless.
    const { container } = renderStrip();
    const tabs = [...container.querySelectorAll('li.tab')];
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData() {} };
    fireEvent.dragStart(tabs[0], { dataTransfer });
    const allowed = fireEvent.dragOver(tabs[1], { dataTransfer });
    expect(allowed).toBe(false);
    expect(dataTransfer.dropEffect).toBe('move');
  });

  test('dragover over an ILLEGAL target is not cancelled', () => {
    // The cursor has to say "no" over a cross-group target rather than let
    // the operator finish a drag that will be refused.
    const { container } = renderStrip(['A-1']);
    const tabs = [...container.querySelectorAll('li.tab')];
    const pinned = tabs.find((el) => el.dataset.taskId === 'A-1');
    const unpinned = tabs.find((el) => el.dataset.taskId === 'A-3');
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData() {} };
    fireEvent.dragStart(pinned, { dataTransfer });
    const allowed = fireEvent.dragOver(unpinned, { dataTransfer });
    expect(allowed).toBe(true);          // nothing called preventDefault
    expect(dataTransfer.dropEffect).toBe('');
  });

  test('the dragged tab and its drop target are marked', () => {
    const { container } = renderStrip();
    const tabs = [...container.querySelectorAll('li.tab')];
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData() {} };
    fireEvent.dragStart(tabs[0], { dataTransfer });
    fireEvent.dragOver(tabs[1], { dataTransfer });
    expect(tabs[0].classList.contains('dragging')).toBe(true);
    expect(tabs[1].classList.contains('drop-target')).toBe(true);
  });

  test('dropping an unpinned tab onto another reorders them', () => {
    const { container } = renderStrip();
    expect(ids(container)).toEqual(['A-1', 'A-2', 'A-3']);
    dragOnto(container, 'A-1', 'A-3');
    expect(ids(container)).toEqual(['A-2', 'A-3', 'A-1']);
  });

  test('the new order survives a remount', () => {
    // It has to be persisted, or the next sessions poll puts it straight back.
    const { container, unmount } = renderStrip();
    dragOnto(container, 'A-1', 'A-3');
    unmount();
    const second = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        activeTaskId="A-1"
        onSelect={() => {}}
      />,
    );
    expect(ids(second.container)).toEqual(['A-2', 'A-3', 'A-1']);
  });

  test('pinned tabs reorder among themselves and stay pinned', () => {
    const { container } = renderStrip(['A-1', 'A-2']);
    expect(ids(container)).toEqual(['A-1', 'A-2', 'A-3']);
    dragOnto(container, 'A-1', 'A-2');
    expect(ids(container)).toEqual(['A-2', 'A-1', 'A-3']);
    // Both are still pinned, and still ahead of the unpinned tab.
    const tabs = [...container.querySelectorAll('li.tab')];
    expect(tabs[0].classList.contains('is-pinned')).toBe(true);
    expect(tabs[1].classList.contains('is-pinned')).toBe(true);
    expect(tabs[2].classList.contains('is-pinned')).toBe(false);
  });

  test('an unpinned tab can never take a pinned tab\u2019s place', () => {
    const { container } = renderStrip(['A-1']);
    dragOnto(container, 'A-3', 'A-1');
    // Refused: nothing moved, and A-1 is still first.
    expect(ids(container)).toEqual(['A-1', 'A-2', 'A-3']);
  });

  test('a pinned tab cannot be dropped among the unpinned ones', () => {
    const { container } = renderStrip(['A-1']);
    dragOnto(container, 'A-1', 'A-3');
    expect(ids(container)).toEqual(['A-1', 'A-2', 'A-3']);
  });

  test('a task that appears later lands after the hand-placed ones', () => {
    const { container, unmount } = renderStrip();
    dragOnto(container, 'A-1', 'A-3');
    unmount();
    const second = render(
      <TabList
        sessions={[
          _session('A-1'), _session('A-2'), _session('A-3'), _session('A-9'),
        ]}
        activeTaskId="A-1"
        onSelect={() => {}}
      />,
    );
    expect(ids(second.container)).toEqual(['A-2', 'A-3', 'A-1', 'A-9']);
  });
});
