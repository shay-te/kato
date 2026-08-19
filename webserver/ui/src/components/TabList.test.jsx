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

  test('pinned tab gets is-pinned class so CSS sticky positioning kicks in', () => {
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

describe('TabList — pinned cluster stacking', () => {

  const TAB_WIDTH = 100;   // fractional-safe stand-in for a measured pill
  const GAP = 6;           // #tab-list > .tab + .tab { margin-left: 6px }

  let restoreRect;

  beforeEach(() => {
    window.localStorage.removeItem(PINNED_TABS_STORAGE_KEY);
    // jsdom reports a zero-size rect for everything, so the layout effect
    // has nothing to accumulate. Give every ``.tab`` a real width.
    const original = HTMLElement.prototype.getBoundingClientRect;
    HTMLElement.prototype.getBoundingClientRect = function rect() {
      if (this.classList && this.classList.contains('tab')) {
        return {
          width: TAB_WIDTH, height: 30, top: 0, left: 0,
          right: TAB_WIDTH, bottom: 30, x: 0, y: 0, toJSON: () => ({}),
        };
      }
      return original.call(this);
    };
    restoreRect = () => { HTMLElement.prototype.getBoundingClientRect = original; };
  });

  afterEach(() => { restoreRect(); });

  function renderPinned(ids) {
    window.localStorage.setItem(PINNED_TABS_STORAGE_KEY, JSON.stringify(ids));
    return render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3'), _session('A-4')]}
        onSelect={() => {}}
      />,
    );
  }

  test('each pinned tab is offset past the full width of the ones before it', () => {
    const { container } = renderPinned(['A-1', 'A-2', 'A-3']);

    const pinned = container.querySelectorAll('li.tab.is-pinned');
    expect(pinned).toHaveLength(3);
    // Anything less than width+gap per step leaves the cluster overlapping.
    expect(pinned[0].style.getPropertyValue('--sticky-left')).toBe('0px');
    expect(pinned[1].style.getPropertyValue('--sticky-left'))
      .toBe(`${TAB_WIDTH + GAP}px`);
    expect(pinned[2].style.getPropertyValue('--sticky-left'))
      .toBe(`${(TAB_WIDTH + GAP) * 2}px`);
  });

  test('a pinned tab never paints over the pinned tabs to its left', () => {
    // The bug: every pinned tab shared one CSS z-index, so at equal z the
    // LAST one in DOM order won and slid on top of its left-hand neighbours.
    const { container } = renderPinned(['A-1', 'A-2', 'A-3']);

    const z = Array.from(container.querySelectorAll('li.tab.is-pinned'))
      .map((el) => Number(el.style.zIndex));

    expect(z[0]).toBeGreaterThan(z[1]);
    expect(z[1]).toBeGreaterThan(z[2]);
  });

  test('pinned tabs outrank the unpinned tabs scrolling underneath', () => {
    const { container } = renderPinned(['A-1']);

    const tabs = container.querySelectorAll('li.tab');
    expect(Number(tabs[0].style.zIndex)).toBeGreaterThan(3);
    expect(tabs[1].style.zIndex).toBe('');
  });

  test('offsets are recomputed when the pinned set changes', () => {
    const { container } = renderPinned(['A-2']);
    expect(container.querySelector('li.tab.is-pinned').style
      .getPropertyValue('--sticky-left')).toBe('0px');

    // Pin A-1 as well — it sorts ahead of A-2, which must shift right.
    const unpinnedPin = container.querySelectorAll('.tab-pin-btn')[1];
    fireEvent.click(unpinnedPin);

    const pinned = container.querySelectorAll('li.tab.is-pinned');
    expect(pinned).toHaveLength(2);
    expect(pinned[1].style.getPropertyValue('--sticky-left'))
      .toBe(`${TAB_WIDTH + GAP}px`);
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
