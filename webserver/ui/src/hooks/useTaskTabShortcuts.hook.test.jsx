// Tests for the Tab / Shift+Tab task-strip navigation hook.
// Contract:
//   - bare Tab → next task, Shift+Tab → previous, both wrapping;
//   - no selection yet: Tab → first, Shift+Tab → last;
//   - never hijacks Tab while typing (editable focus) or while a
//     modal / settings drawer is open, or with Ctrl/Cmd/Alt held;
//   - claimed events get preventDefault so DOM focus doesn't also move.

import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useTaskTabShortcuts } from './useTaskTabShortcuts.js';

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
    ['ctrl', { ctrl: true }],
    ['meta', { meta: true }],
    ['alt', { alt: true }],
  ])('Tab with %s held is left to the browser', (_label, mods) => {
    mount({ sessions: SESSIONS, activeTaskId: 'A', onSelect });
    press('Tab', mods);
    expect(onSelect).not.toHaveBeenCalled();
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
});
