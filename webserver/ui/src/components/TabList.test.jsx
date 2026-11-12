// Tests for TabList. Maps sessions to Tabs in a <ul> and renders
// the header buttons (Add task, Scan now). Empty state shows when
// the sessions list is empty.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';

import TabList from './TabList.jsx';
import {
  TASK_MENU_CLOSE_GRACE_MS, TASK_MENU_OPEN_DELAY_MS, _resetTaskMenu, openTaskMenu,
} from '../stores/taskMenuStore.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import {
  PINNED_TABS_STORAGE_KEY,
  readPinnedIds,
} from '../utils/pinnedTabs.js';
import {
  COLLAPSED_GROUPS_STORAGE_KEY,
  readCollapsedGroups,
} from '../utils/pinnedTabs.js';


// The menu's open state is module-wide: never let one test's hover leak.
beforeEach(() => { _resetTaskMenu(); });

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

  test('with no ticket system, the empty strip offers one thing: create the first task', () => {
    const onNewTask = vi.fn();
    render(<TabList sessions={[]} onSelect={() => {}} onNewTask={onNewTask} />);
    expect(screen.getByText(/No tasks yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create your first task' }));
    expect(onNewTask).toHaveBeenCalledTimes(1);
    // Nothing about a tracker this kato does not have.
    expect(screen.queryByText('+ Add task')).toBeNull();
    expect(screen.queryByText(/YouTrack|wait-planning/)).toBeNull();
  });

  test('with a ticket system, it is named, beside creating a task', () => {
    render(<TabList sessions={[]} onSelect={() => {}} onNewTask={() => {}} trackerName="Jira" />);
    expect(screen.getByRole('button', { name: 'Create your first task' })).toBeInTheDocument();
    // "+ Add task" appears as a strong inside the empty-state copy
    // (separate from the header button).
    expect(screen.getByText('+ Add task')).toBeInTheDocument();
    expect(screen.getByText(/assigned Jira tickets/)).toBeInTheDocument();
  });

  test('renders the empty-state when sessions is undefined (defensive)', () => {
    render(<TabList sessions={undefined} onSelect={() => {}} />);
    expect(screen.getByText(/No tasks yet/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create your first task' })).toBeDisabled();
  });

  test('New task button fires onNewTask', () => {
    const onNewTask = vi.fn();
    render(<TabList sessions={[]} onSelect={() => {}} onNewTask={onNewTask} />);
    fireEvent.click(screen.getByRole('button', { name: 'New task' }));
    expect(onNewTask).toHaveBeenCalledTimes(1);
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

  // jsdom never moves a scroller, a real browser does. The harness gives the
  // strip a live scrollLeft and makes ``scrollIntoView`` jump it the way a
  // browser would, so "the strip did not move" is something a test can see.
  function withFrames(run) {
    const frames = [];
    vi.stubGlobal('requestAnimationFrame', (fn) => { frames.push(fn); return frames.length; });
    const origSIV = window.HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      run({ scrollIntoView, flushFrames: () => frames.splice(0).forEach((fn) => fn(0)) });
    } finally {
      window.HTMLElement.prototype.scrollIntoView = origSIV;
      vi.unstubAllGlobals();
    }
  }

  test('pinning still follows the tab to the pinned block', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        onSelect={() => {}}
      />,
    );
    withFrames(({ scrollIntoView, flushFrames }) => {
      fireEvent.click(container.querySelectorAll('.tab-pin-btn')[2]); // pin A-3
      flushFrames();
      expect(tabOrder(container)).toEqual(['A-3', 'A-1', 'A-2']);
      expect(scrollIntoView).toHaveBeenCalledTimes(1);
      expect(scrollIntoView.mock.contexts[0].dataset.taskId).toBe('A-3');
    });
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
    // The Ctrl+Shift+P shortcut is the fast path, but a shortcut nobody is told
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
    expect(button.getAttribute('data-tooltip')).toMatch(/Ctrl\+Shift\+P/);
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

describe('TabList empty state tells the three cases apart', () => {
  // An empty strip used to render the onboarding copy no matter WHY it was
  // empty. Reloading the page during kato's ~24s startup therefore said
  // "No tabs yet. Click + Add task" — indistinguishable from having had every
  // task deleted, while the workspaces sat untouched on disk.
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); });

  test('an unreachable kato says so, and never says "No tabs yet"', () => {
    render(
      <TabList sessions={[]} sessionsLoaded={false} katoReachable={false} onSelect={() => {}} />,
    );
    expect(screen.getByText(/Can’t reach kato/)).toBeInTheDocument();
    expect(screen.queryByText(/No tasks yet/)).not.toBeInTheDocument();
    // The reassurance matters: this is the moment operators think data is gone.
    expect(screen.getByText(/workspaces are untouched/)).toBeInTheDocument();
  });

  test('a still-loading strip does not claim the operator has no tasks', () => {
    render(
      <TabList sessions={[]} sessionsLoaded={false} katoReachable onSelect={() => {}} />,
    );
    expect(screen.queryByText(/No tasks yet/)).not.toBeInTheDocument();
  });

  test('a fast first load never flashes a loading message', () => {
    render(
      <TabList sessions={[]} sessionsLoaded={false} katoReachable onSelect={() => {}} />,
    );
    // Before the grace period elapses the strip stays quiet.
    expect(screen.queryByText(/Loading your tasks/)).not.toBeInTheDocument();
  });

  test('a slow first load eventually explains itself', async () => {
    render(
      <TabList sessions={[]} sessionsLoaded={false} katoReachable onSelect={() => {}} />,
    );
    await vi.advanceTimersByTimeAsync(700);
    expect(await screen.findByText(/Loading your tasks/)).toBeInTheDocument();
    expect(screen.queryByText(/No tasks yet/)).not.toBeInTheDocument();
  });

  test('a loaded, reachable, genuinely empty kato still onboards', () => {
    render(
      <TabList sessions={[]} sessionsLoaded katoReachable onSelect={() => {}} />,
    );
    expect(screen.getByText(/No tasks yet/)).toBeInTheDocument();
  });
});


// A split plan opens a tab per sub-task; they fold into the main task's tab,
// and selecting that family opens a second row of sub-task tabs under the strip.
describe('TabList — sub-tasks are grouped under their main task', () => {
  const SESSIONS = [
    _session('UNA-1'),
    _session('LOCAL-4', { plan_parent: 'UNA-1', task_summary: 'UNA-1 · Task 1: The endpoint' }),
    _session('UNA-7', { task_summary: 'Subtask of UNA-1: The page' }),
    _session('B-2'),
  ];
  const ids = (nodes) => [...nodes].map((li) => li.dataset.taskId);
  const outerIds = (container) => ids(container.querySelectorAll('#tab-list > li.tab'));
  const group = (name = 'Sub-tasks of UNA-1') => screen.queryByRole('list', { name });

  test('the main task is a row, its sub-tasks are rows under it', () => {
    const { container } = render(<TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={() => {}} />);
    expect(outerIds(container)).toEqual(['UNA-1', 'B-2']);
    const rows = group().querySelectorAll('li.tab');
    expect(ids(rows)).toEqual(['LOCAL-4', 'UNA-7']);
    rows.forEach((row) => expect(row).toHaveClass('is-nested'));
    expect(group()).toHaveTextContent('The page');  // named without "Subtask of UNA-1:"
    // Grouped whichever task is open — the menu is a list to scan, not a
    // strip that has to stay short.
    expect(container.querySelector('.has-open-subtask')).toBeNull();
  });

  test('Tab walks the order the eye reads: each task, then its sub-tasks', () => {
    const onVisibleOrderChange = vi.fn();
    render(
      <TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={() => {}}
        onVisibleOrderChange={onVisibleOrderChange} />,
    );
    expect(onVisibleOrderChange).toHaveBeenLastCalledWith(['UNA-1', 'LOCAL-4', 'UNA-7', 'B-2']);
  });

  test('a sub-task row selects that sub-task, and shows it is the open one', () => {
    const onSelect = vi.fn();
    const { rerender } = render(<TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={onSelect} />);
    fireEvent.click(group().querySelector('li[data-task-id="UNA-7"]'));
    expect(onSelect).toHaveBeenCalledWith('UNA-7');
    rerender(<TabList sessions={SESSIONS} activeTaskId="UNA-7" onSelect={onSelect} />);
    expect(group().querySelector('li[data-task-id="UNA-7"]')).toHaveClass('active');
  });

  test('a sub-task waiting for you marks its own row', () => {
    render(
      <TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={() => {}}
        attentionTaskIds={new Set(['LOCAL-4'])} />,
    );
    expect(group().querySelector('li[data-task-id="LOCAL-4"]')).toHaveClass('needs-attention');
  });

  test('a pinned sub-task keeps its place among the pins and is not drawn twice', () => {
    window.localStorage.setItem(PINNED_TABS_STORAGE_KEY, JSON.stringify(['UNA-7']));
    try {
      const { container } = render(<TabList sessions={SESSIONS} activeTaskId="UNA-1" onSelect={() => {}} />);
      expect(outerIds(container)).toEqual(['UNA-7', 'UNA-1', 'B-2']);
      expect(ids(group().querySelectorAll('li.tab'))).toEqual(['LOCAL-4']);
    } finally {
      window.localStorage.removeItem(PINNED_TABS_STORAGE_KEY);
    }
  });

  test('a sub-task whose main task has no row here is a row of its own', () => {
    const { container } = render(
      <TabList sessions={SESSIONS.slice(1)} activeTaskId="UNA-7" onSelect={() => {}} />,
    );
    expect(outerIds(container)).toEqual(['LOCAL-4', 'UNA-7', 'B-2']);
    expect(group()).toBeNull();
  });

  test('pinning a sub-task, then unpinning it, puts it back under its main task', () => {
    const { container } = render(<TabList sessions={SESSIONS} activeTaskId="UNA-7" onSelect={() => {}} />);
    fireEvent.click(group().querySelector('li[data-task-id="UNA-7"] .tab-pin-btn'));
    expect(outerIds(container)).toEqual(['UNA-7', 'UNA-1', 'B-2']);
    expect(ids(group().querySelectorAll('li.tab'))).toEqual(['LOCAL-4']);
    fireEvent.click(container.querySelector('#tab-list > li[data-task-id="UNA-7"] .tab-pin-btn'));
    expect(outerIds(container)).toEqual(['UNA-1', 'B-2']);
    expect(ids(group().querySelectorAll('li.tab'))).toEqual(['LOCAL-4', 'UNA-7']);
  });

  test('a sub-task cannot be dragged out among the main tasks', () => {
    const { container } = render(<TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={() => {}} />);
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData() {} };
    fireEvent.dragStart(group().querySelector('li[data-task-id="UNA-7"]'), { dataTransfer });
    // Not cancelled = not a legal target (the cursor says "no").
    expect(fireEvent.dragOver(container.querySelector('#tab-list > li[data-task-id="B-2"]'), { dataTransfer })).toBe(true);
    // Among its siblings it can.
    expect(fireEvent.dragOver(group().querySelector('li[data-task-id="LOCAL-4"]'), { dataTransfer })).toBe(false);
  });
});

describe('TabList — the tasks menu opens from the kato icon', () => {
  const SESSIONS = [_session('A-1'), _session('A-2')];
  const pane = (container) => container.querySelector('#tabs-pane');

  afterEach(() => { vi.useRealTimers(); });

  test('collapsed until hovered, its rows always there; expanded once open', () => {
    const { container } = render(<TabList sessions={SESSIONS} onSelect={() => {}} />);
    expect(pane(container)).toHaveClass('tabs-pane-side');
    expect(pane(container)).not.toHaveClass('is-open');
    // Collapsed is not hidden: the rail shows every task (by its code).
    expect(container.querySelectorAll('#tab-list li.tab')).toHaveLength(2);
    act(() => openTaskMenu());  // what pointing at the kato logo does
    expect(pane(container)).toHaveClass('is-open');
  });

  test('a pointer only passing over the rail does not expand it', () => {
    vi.useFakeTimers();
    const { container } = render(<TabList sessions={SESSIONS} onSelect={() => {}} />);
    fireEvent.mouseEnter(pane(container));
    act(() => { vi.advanceTimersByTime(TASK_MENU_OPEN_DELAY_MS - 50); });
    fireEvent.mouseLeave(pane(container));
    act(() => { vi.advanceTimersByTime(TASK_MENU_OPEN_DELAY_MS + TASK_MENU_CLOSE_GRACE_MS); });
    expect(pane(container)).not.toHaveClass('is-open');
  });

  test('leaving the menu closes it after a moment; coming back in time keeps it open', () => {
    vi.useFakeTimers();
    const { container } = render(<TabList sessions={SESSIONS} onSelect={() => {}} />);
    fireEvent.mouseEnter(pane(container));
    act(() => { vi.advanceTimersByTime(TASK_MENU_OPEN_DELAY_MS); });
    expect(pane(container)).toHaveClass('is-open');
    fireEvent.mouseLeave(pane(container));
    act(() => { vi.advanceTimersByTime(TASK_MENU_CLOSE_GRACE_MS - 50); });
    expect(pane(container)).toHaveClass('is-open');
    fireEvent.mouseEnter(pane(container));  // back before the grace ran out
    act(() => { vi.advanceTimersByTime(TASK_MENU_CLOSE_GRACE_MS * 2); });
    expect(pane(container)).toHaveClass('is-open');
    fireEvent.mouseLeave(pane(container));
    act(() => { vi.advanceTimersByTime(TASK_MENU_CLOSE_GRACE_MS + 1); });
    expect(pane(container)).not.toHaveClass('is-open');
  });

  test('Escape, or a press outside the menu, closes it', () => {
    const { container } = render(<TabList sessions={SESSIONS} onSelect={() => {}} />);
    act(() => openTaskMenu());
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(pane(container)).not.toHaveClass('is-open');
    act(() => openTaskMenu());
    fireEvent.mouseDown(container.querySelector('li.tab'));
    expect(pane(container)).toHaveClass('is-open');  // inside: stays
    fireEvent.mouseDown(document.body);
    expect(pane(container)).not.toHaveClass('is-open');
  });

  test('with no task at all it is held open, so the first task is one click away', () => {
    const { container } = render(<TabList sessions={[]} onSelect={() => {}} onNewTask={() => {}} />);
    expect(pane(container)).toHaveClass('is-open');
    expect(screen.getByRole('button', { name: 'Create your first task' })).toBeInTheDocument();
  });

  test('the actions head the menu, above the rows', () => {
    const { container } = render(<TabList sessions={SESSIONS} onSelect={() => {}} />);
    const children = [...pane(container).children];
    expect(children[0]).toHaveClass('tabs-actions');
    expect(children[1].id).toBe('tab-list');
  });

  test('the app\'s chrome: the logo heads the rail, the footer is its foot', () => {
    const { container } = render(
      <TabList sessions={SESSIONS} onSelect={() => {}}
        handle={<div data-testid="handle" />} footer={<div data-testid="footer" />} />,
    );
    const children = [...pane(container).children];
    expect(children[0]).toBe(screen.getByTestId('handle'));
    expect(children[1]).toHaveClass('tabs-actions');
    expect(children[2].id).toBe('tab-list');
    expect(children.at(-1)).toBe(screen.getByTestId('footer'));
  });

  test('the actions\' hover labels open from their left edge: the rail is the screen\'s', () => {
    const { container } = render(<TabList sessions={SESSIONS} onSelect={() => {}} />);
    container.querySelectorAll('.tabs-action').forEach((button) => {
      expect(button).toHaveClass('tooltip-anchor-left');
    });
  });
});

describe('TabList — a main task\'s sub-tasks fold away', () => {
  const SESSIONS = [
    _session('UNA-1'),
    _session('UNA-7', { task_summary: 'Subtask of UNA-1: The page' }),
    _session('UNA-8', { task_summary: 'Subtask of UNA-1: The email' }),
    _session('B-2'),
  ];
  const ids = (nodes) => [...nodes].map((li) => li.dataset.taskId);
  const group = () => screen.queryByRole('list', { name: 'Sub-tasks of UNA-1' });
  const toggle = (container) => container.querySelector('li[data-task-id="UNA-1"] .tab-subtasks-toggle');

  beforeEach(() => { window.localStorage.removeItem(COLLAPSED_GROUPS_STORAGE_KEY); });
  afterEach(() => { window.localStorage.removeItem(COLLAPSED_GROUPS_STORAGE_KEY); });

  test('the main task\'s row folds its sub-tasks away and back, without opening it', () => {
    const onSelect = vi.fn();
    const onVisibleOrderChange = vi.fn();
    const { container } = render(
      <TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={onSelect}
        onVisibleOrderChange={onVisibleOrderChange} />,
    );
    const button = toggle(container);
    expect(button).toHaveTextContent('2');
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(button).toHaveAttribute('aria-controls', group().id);
    expect(button).toHaveAccessibleName('Hide the 2 sub-tasks of UNA-1');

    fireEvent.click(button);
    expect(group()).toBeNull();
    expect(onSelect).not.toHaveBeenCalled();
    expect(toggle(container)).toHaveAttribute('aria-expanded', 'false');
    expect(toggle(container)).toHaveClass('is-folded');
    expect(toggle(container)).toHaveAccessibleName('Show the 2 sub-tasks of UNA-1');
    expect(readCollapsedGroups(window.localStorage)).toEqual(['UNA-1']);
    // Tab / Ctrl+Tab still walk every task, folded or not.
    expect(onVisibleOrderChange).toHaveBeenLastCalledWith(['UNA-1', 'UNA-7', 'UNA-8', 'B-2']);

    fireEvent.click(toggle(container));
    expect(ids(group().querySelectorAll('li.tab'))).toEqual(['UNA-7', 'UNA-8']);
    expect(readCollapsedGroups(window.localStorage)).toEqual([]);
  });

  test('a fold is remembered across a reload', () => {
    window.localStorage.setItem(COLLAPSED_GROUPS_STORAGE_KEY, JSON.stringify(['UNA-1']));
    const { container } = render(<TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={() => {}} />);
    expect(group()).toBeNull();
    expect(toggle(container)).toHaveAttribute('aria-expanded', 'false');
  });

  test('a folded group still shows the sub-task on screen', () => {
    window.localStorage.setItem(COLLAPSED_GROUPS_STORAGE_KEY, JSON.stringify(['UNA-1']));
    const { container } = render(<TabList sessions={SESSIONS} activeTaskId="UNA-8" onSelect={() => {}} />);
    expect(ids(group().querySelectorAll('li.tab'))).toEqual(['UNA-8']);
    expect(toggle(container)).toHaveTextContent('2');  // still counts them all
  });

  test('a folded sub-task waiting for you shows on the toggle; the one on screen is not counted', () => {
    window.localStorage.setItem(COLLAPSED_GROUPS_STORAGE_KEY, JSON.stringify(['UNA-1']));
    const { container, rerender } = render(
      <TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={() => {}}
        attentionTaskIds={new Set(['UNA-7'])} />,
    );
    expect(toggle(container)).toHaveClass('has-attention');
    expect(toggle(container)).toHaveAccessibleName('Show the 2 sub-tasks of UNA-1 — 1 waiting for you');
    rerender(
      <TabList sessions={SESSIONS} activeTaskId="UNA-7" onSelect={() => {}}
        attentionTaskIds={new Set(['UNA-7'])} />,
    );
    expect(toggle(container)).not.toHaveClass('has-attention');
    // Unfolded, every row marks itself: nothing to count on the toggle.
    fireEvent.click(toggle(container));
    expect(toggle(container)).not.toHaveClass('has-attention');
  });

  test('only a main task with sub-tasks has the toggle', () => {
    const { container } = render(<TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={() => {}} />);
    expect(container.querySelectorAll('.tab-subtasks-toggle')).toHaveLength(1);
    expect(container.querySelector('li[data-task-id="B-2"] .tab-subtasks-toggle')).toBeNull();
    expect(group().querySelector('.tab-subtasks-toggle')).toBeNull();
  });

  test('folding forgets tasks that are gone', () => {
    window.localStorage.setItem(COLLAPSED_GROUPS_STORAGE_KEY, JSON.stringify(['GONE-1']));
    const { container } = render(<TabList sessions={SESSIONS} activeTaskId="B-2" onSelect={() => {}} />);
    fireEvent.click(toggle(container));
    expect(readCollapsedGroups(window.localStorage)).toEqual(['UNA-1']);
  });
});
