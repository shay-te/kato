// Tests for TabList. Maps sessions to Tabs in a <ul> and renders
// the header buttons (Add task, Scan now). Empty state shows when
// the sessions list is empty.

import { describe, test, expect, vi, beforeEach } from 'vitest';
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
