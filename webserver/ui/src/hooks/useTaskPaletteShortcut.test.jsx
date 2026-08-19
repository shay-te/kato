// Ctrl/Cmd+Shift+F opens the task palette.
//
// The key choice is load-bearing, so it is pinned here. NOT Ctrl+P:
// RightPane already binds Ctrl/Cmd+P to focus the workspace FILE filter,
// so the palette on that key double-bound it — both handlers fired and
// the operator got the palette on top of a focused file search. Ctrl+P
// is also VS Code's "Go to File", so file search is the meaning already
// in everyone's fingers.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

import { useTaskPaletteShortcut } from './useTaskPaletteShortcut.js';

function Harness({ onOpen }) {
  useTaskPaletteShortcut(onOpen);
  return <input aria-label="composer" />;
}

function press(init) {
  const event = new KeyboardEvent('keydown', {
    key: 'f', bubbles: true, cancelable: true, ...init,
  });
  window.dispatchEvent(event);
  return event;
}

function pressP(init) {
  const event = new KeyboardEvent('keydown', {
    key: 'p', bubbles: true, cancelable: true, ...init,
  });
  window.dispatchEvent(event);
  return event;
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

describe('useTaskPaletteShortcut', () => {
  test('Ctrl+Shift+F opens the palette', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    press({ ctrlKey: true, shiftKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  test('Cmd+Shift+F opens it on macOS', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    press({ metaKey: true, shiftKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  test('Ctrl+P is NOT claimed — it belongs to the workspace file filter', () => {
    // The bug this replaced: both handlers fired on Ctrl+P, so the
    // operator got the palette on top of a focused file search.
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const event = pressP({ ctrlKey: true });
    expect(onOpen).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('Ctrl+F alone is left to the browser find bar', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const event = press({ ctrlKey: true });
    expect(onOpen).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('a bare f is left alone so typing still works', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const event = press({});
    expect(onOpen).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('Alt is left alone too', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    press({ ctrlKey: true, shiftKey: true, altKey: true });
    expect(onOpen).not.toHaveBeenCalled();
  });

  test('it fires while focus is in a text field', () => {
    // Unlike the Tab task-cycling shortcut, this one must work in the
    // composer: that is where the cursor spends nearly all its time, so
    // a "go to task" gesture that refused there would refuse exactly
    // when it is wanted. Safe because Ctrl/Cmd is held — it cannot be
    // someone typing.
    const onOpen = vi.fn();
    const { getByLabelText } = render(<Harness onOpen={onOpen} />);
    getByLabelText('composer').focus();
    press({ ctrlKey: true, shiftKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  test('it stands down while a modal owns the keyboard', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    document.body.appendChild(dialog);
    press({ ctrlKey: true, shiftKey: true });
    expect(onOpen).not.toHaveBeenCalled();
  });

  test('the listener is removed on unmount', () => {
    const onOpen = vi.fn();
    const { unmount } = render(<Harness onOpen={onOpen} />);
    unmount();
    press({ ctrlKey: true, shiftKey: true });
    expect(onOpen).not.toHaveBeenCalled();
  });
});
