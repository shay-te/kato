// Tests for the Tab / Ctrl+Tab task navigation hook.
// Contract:
//   - Tab or Ctrl+Tab → next task, Shift (+Ctrl) +Tab → previous, wrapping;
//   - no selection yet: Tab → first, Shift+Tab → last;
//   - a bare Tab is never hijacked while typing (editable focus); Ctrl+Tab
//     is (nobody moves focus with it); neither while a modal / settings
//     drawer is open, nor with Cmd/Alt held;
//   - claimed events get preventDefault so DOM focus doesn't also move.

import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import { useTaskTabShortcuts } from './useTaskTabShortcuts.js';
import {
  TASK_SWITCHER_REVEAL_MS, setTaskSwitcherShown, useTaskSwitcherVisible,
} from '../stores/taskSwitchStore.js';

const SESSIONS = [
  { task_id: 'A' }, { task_id: 'B' }, { task_id: 'C' },
];

function press(key, { shift = false, ctrl = false, meta = false,
  alt = false, target = window } = {}) {
  const event = new KeyboardEvent('keydown', {
    key, shiftKey: shift, ctrlKey: ctrl, metaKey: meta, altKey: alt,
    bubbles: true, cancelable: true,
  });
  target.dispatchEvent(event);
  return event;
}

function release(key, target = window) {
  target.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true, cancelable: true }));
}

function mount(props) {
  return renderHook((p) => useTaskTabShortcuts(p), { initialProps: props });
}

describe('useTaskTabShortcuts', () => {
  let onSelect;

  beforeEach(() => {
    onSelect = vi.fn();
    document.body.innerHTML = '';
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  test('Tab selects the next task', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab');
    expect(onSelect).toHaveBeenCalledWith('B');
  });

  test('Shift+Tab selects the previous task', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'B', onSelect });
    press('Tab', { shift: true });
    expect(onSelect).toHaveBeenCalledWith('A');
  });

  test('Tab wraps from last to first', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'C', onSelect });
    press('Tab');
    expect(onSelect).toHaveBeenCalledWith('A');
  });

  test('Shift+Tab wraps from first to last', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab', { shift: true });
    expect(onSelect).toHaveBeenCalledWith('C');
  });

  test('no selection: Tab picks the first task', () => {
    mount({ sessions: SESSIONS, activeTaskId: '', onSelect });
    press('Tab');
    expect(onSelect).toHaveBeenCalledWith('A');
  });

  test('no selection: Shift+Tab picks the last task', () => {
    mount({ sessions: SESSIONS, activeTaskId: '', onSelect });
    press('Tab', { shift: true });
    expect(onSelect).toHaveBeenCalledWith('C');
  });

  test('empty session list is a no-op', () => {
    const e = (() => {
      mount({ sessions: [], activeTaskId: '', onSelect });
      return press('Tab');
    })();
    expect(onSelect).not.toHaveBeenCalled();
    expect(e.defaultPrevented).toBe(false);
  });

  test('single task: no redundant onSelect for the already-active tab', () => {
    mount({ sessions: [{ task_id: 'A' }], activeTaskId: 'A', onSelect });
    press('Tab');
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('claimed Tab is preventDefaulted', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    const e = press('Tab');
    expect(e.defaultPrevented).toBe(true);
  });

  test('does not hijack Tab while typing in an input', () => {
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    const e = press('Tab', { target: input });
    expect(onSelect).not.toHaveBeenCalled();
    expect(e.defaultPrevented).toBe(false);
  });

  test('does not hijack Tab inside a textarea', () => {
    const ta = document.createElement('textarea');
    document.body.appendChild(ta);
    ta.focus();
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab', { target: ta });
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('does not hijack Tab in a contenteditable region', () => {
    const div = document.createElement('div');
    div.setAttribute('contenteditable', 'true');
    // jsdom does not derive isContentEditable from the attribute;
    // emulate the browser-resolved property the hook actually reads.
    Object.defineProperty(div, 'isContentEditable', { value: true });
    document.body.appendChild(div);
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab', { target: div });
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('does not hijack Tab while a modal dialog is open', () => {
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    document.body.appendChild(dialog);
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab');
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('does not hijack Tab while the settings drawer is open', () => {
    const drawer = document.createElement('div');
    drawer.className = 'settings-drawer is-open';
    document.body.appendChild(drawer);
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab');
    expect(onSelect).not.toHaveBeenCalled();
  });

  test.each([
    ['meta', { meta: true }],
    ['alt', { alt: true }],
  ])('Tab with %s held is left to the browser', (_label, mods) => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab', mods);
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('Ctrl+Tab and Ctrl+Shift+Tab walk the tasks like Tab', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'B', onSelect });
    const next = press('Tab', { ctrl: true });
    expect(onSelect).toHaveBeenLastCalledWith('C');
    expect(next.defaultPrevented).toBe(true);
    press('Tab', { ctrl: true, shift: true });
    expect(onSelect).toHaveBeenLastCalledWith('A');
  });

  test('Ctrl+Tab switches tasks even from a text field; a bare Tab there does not', () => {
    const input = document.createElement('textarea');
    document.body.appendChild(input);
    input.focus();
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab', { target: input });
    expect(onSelect).not.toHaveBeenCalled();
    press('Tab', { ctrl: true, target: input });
    expect(onSelect).toHaveBeenCalledWith('B');
  });

  test('non-Tab keys are ignored', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Enter');
    press('ArrowRight');
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('listener is removed on unmount', () => {
    const { unmount } = mount({
      sessions: SESSIONS, activeTaskId: 'A', onSelect,
    });
    unmount();
    press('Tab');
    expect(onSelect).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------
  // Tab must follow the STRIP's order, not the raw session list.
  //
  // The tab strip sorts pinned tabs to the front and applies the
  // operator's drag arrangement to the rest; App only had the unsorted
  // ``sessions``. So after a drag the eye followed one order and Tab
  // walked another: "I dragged a task from last to be second, but when
  // clicking the tab button on the keyboard he is moving to the tab in
  // the old order".
  // -------------------------------------------------------------------

  test('Tab follows the visible order after a drag, not the session order', () => {
    const onSelect = vi.fn();
    // Strip shows C second (dragged from last); sessions still say A,B,C.
    mount({
      sessions: SESSIONS,
      visibleOrder: ['A', 'C', 'B'],
      activeTaskId: 'A',
      onSelect,
    });
    press('Tab');
    expect(onSelect).toHaveBeenCalledWith('C');
  });

  test('Shift+Tab also follows the visible order', () => {
    const onSelect = vi.fn();
    mount({
      sessions: SESSIONS,
      visibleOrder: ['A', 'C', 'B'],
      activeTaskId: 'B',
      onSelect,
    });
    press('Tab', { shift: true });
    expect(onSelect).toHaveBeenCalledWith('C');
  });

  test('the visible order wraps at its own ends', () => {
    const onSelect = vi.fn();
    mount({
      sessions: SESSIONS,
      visibleOrder: ['A', 'C', 'B'],
      activeTaskId: 'B',
      onSelect,
    });
    press('Tab');
    expect(onSelect).toHaveBeenCalledWith('A');
  });

  test('falls back to the session order before the strip has reported', () => {
    const onSelect = vi.fn();
    mount({
      sessions: SESSIONS, visibleOrder: [], activeTaskId: 'A', onSelect,
    });
    press('Tab');
    expect(onSelect).toHaveBeenCalledWith('B');
  });
});

// A quick press only switches; the switcher overlay shows only while the press
// is HELD past TASK_SWITCHER_REVEAL_MS, and goes when it is let go.
describe('useTaskTabShortcuts: the switcher shows only on a hold', () => {
  let onSelect;
  let shown;

  beforeEach(() => {
    vi.useFakeTimers();
    onSelect = vi.fn();
    document.body.innerHTML = '';
    shown = renderHook(() => useTaskSwitcherVisible()).result;
  });

  afterEach(() => {
    act(() => setTaskSwitcherShown(false));
    vi.useRealTimers();
  });

  const wait = (ms) => act(() => { vi.advanceTimersByTime(ms); });

  test('a quick Tab switches with nothing on screen', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab');
    wait(TASK_SWITCHER_REVEAL_MS - 50);
    act(() => release('Tab'));
    wait(1000);
    expect(onSelect).toHaveBeenCalledWith('B');
    expect(shown.current).toBe(false);
  });

  test('Tab held shows the switcher until it is let go', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab');
    wait(TASK_SWITCHER_REVEAL_MS);
    expect(shown.current).toBe(true);
    act(() => release('Tab'));
    expect(shown.current).toBe(false);
  });

  test('held, the hold outlives the switch it made (the hook re-renders on the new task)', () => {
    const view = mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab');
    view.rerender({ sessions: SESSIONS, activeTaskId: 'B', onSelect });
    wait(TASK_SWITCHER_REVEAL_MS);
    expect(shown.current).toBe(true);
    press('Tab');  // auto-repeat goes on through the tasks
    expect(onSelect).toHaveBeenLastCalledWith('C');
    act(() => release('Tab'));
    expect(shown.current).toBe(false);
  });

  test('Ctrl+Tab lasts while Ctrl does: Tab can be tapped on through the tasks', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab', { ctrl: true });
    act(() => release('Tab'));
    wait(TASK_SWITCHER_REVEAL_MS);
    expect(shown.current).toBe(true);
    act(() => release('Control'));
    expect(shown.current).toBe(false);
  });

  test('a quick Ctrl+Tab shows nothing', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab', { ctrl: true });
    act(() => { release('Tab'); release('Control'); });
    wait(1000);
    expect(onSelect).toHaveBeenCalledWith('B');
    expect(shown.current).toBe(false);
  });

  test('leaving the window ends a hold (its keyup never comes)', () => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab');
    wait(TASK_SWITCHER_REVEAL_MS);
    act(() => window.dispatchEvent(new Event('blur')));
    expect(shown.current).toBe(false);
  });

  test('unmounting mid-hold hides it', () => {
    const view = mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab');
    wait(TASK_SWITCHER_REVEAL_MS);
    act(() => view.unmount());
    expect(shown.current).toBe(false);
  });

  test('a Tab left to a text field holds nothing', () => {
    const input = document.createElement('textarea');
    document.body.appendChild(input);
    input.focus();
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab', { target: input });
    wait(1000);
    expect(shown.current).toBe(false);
  });
});
