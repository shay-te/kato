// Ctrl/Cmd+P opens the task palette.
//
// The key choice is load-bearing, so it is pinned here: Ctrl+P is the
// browser's Print shortcut but is cleanly overridable with
// preventDefault, and inside a code surface the muscle memory for it is
// "find a thing" (VS Code makes the same trade for Go to File).
// Ctrl+SHIFT+P is deliberately left alone — Firefox opens a private
// window at the browser-chrome level, where a page cannot intercept it,
// so claiming it would silently fail for Firefox operators.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

import { useTaskPaletteShortcut } from './useTaskPaletteShortcut.js';

function Harness({ onOpen }) {
  useTaskPaletteShortcut(onOpen);
  return <input aria-label="composer" />;
}

function press(init) {
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
  test('Ctrl+P opens the palette', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    press({ ctrlKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  test('Cmd+P opens it on macOS', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    press({ metaKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  test('the browser print dialog is suppressed', () => {
    render(<Harness onOpen={vi.fn()} />);
    const event = press({ ctrlKey: true });
    expect(event.defaultPrevented).toBe(true);
  });

  test('a bare p is left alone so typing still works', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const event = press({});
    expect(onOpen).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('Ctrl+Shift+P is NOT claimed — Firefox owns it for private windows', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const event = press({ ctrlKey: true, shiftKey: true });
    expect(onOpen).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('Alt+P is left alone too', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    press({ ctrlKey: true, altKey: true });
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
    press({ ctrlKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  test('it stands down while a modal owns the keyboard', () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    document.body.appendChild(dialog);
    press({ ctrlKey: true });
    expect(onOpen).not.toHaveBeenCalled();
  });

  test('the listener is removed on unmount', () => {
    const onOpen = vi.fn();
    const { unmount } = render(<Harness onOpen={onOpen} />);
    unmount();
    press({ ctrlKey: true });
    expect(onOpen).not.toHaveBeenCalled();
  });
});
