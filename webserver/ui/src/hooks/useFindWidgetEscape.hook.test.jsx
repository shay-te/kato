// Tests for ``useFindWidgetEscape`` — the focus-independent Escape that
// closes Monaco's Ctrl+F find bar.
//
// The bug it exists for: Monaco binds Escape→closeFindWidget behind
// ``EditorContextKeys.focus``, so once focus leaves the editor the find bar
// can no longer be dismissed with Escape at all. Verified against Monaco 0.55
// in a real browser — focus inside the find input closes fine, focus anywhere
// else does nothing — which left operators stuck with the bar pinned over the
// file (its 16×16 ✕ being the only exit, and unreliable in WebView2).
//
// So the load-bearing behaviors are:
//   - Escape closes the widget even when the event originates OUTSIDE the
//     editor (this is the whole point — hence the capture-phase window listener).
//   - It only fires when the widget is actually open.
//   - An open modal/drawer keeps Escape for itself.
//   - It survives Monaco internals changing shape (controller missing/throwing
//     → DOM fallback) and never throws.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useFindWidgetEscape } from './useFindWidgetEscape.js';

function fakeEditor({ isRevealed = true, controller = true, throws = false, domOpen = null } = {}) {
  const dom = document.createElement('div');
  if (domOpen !== null) {
    const w = document.createElement('div');
    w.className = domOpen ? 'find-widget visible' : 'find-widget';
    dom.appendChild(w);
  }
  return {
    trigger: vi.fn(),
    getDomNode: () => dom,
    getContribution: (id) => {
      if (throws) { throw new Error('contribution blew up'); }
      if (!controller || id !== 'editor.contrib.findController') { return null; }
      return { getState: () => ({ isRevealed }) };
    },
  };
}

function press(key, target = document.body) {
  target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

function mount(editor) {
  const ref = { current: editor };
  const view = renderHook(() => useFindWidgetEscape(ref));
  return { ref, view };
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('useFindWidgetEscape', () => {
  test('Escape closes the find widget when the event comes from OUTSIDE the editor', () => {
    // The regression: Monaco itself ignores Escape here because the editor
    // does not hold focus, so the bar could never be dismissed.
    const editor = fakeEditor({ isRevealed: true });
    mount(editor);
    const outside = document.createElement('input');
    document.body.appendChild(outside);
    outside.focus();

    press('Escape', outside);

    expect(editor.trigger).toHaveBeenCalledTimes(1);
    expect(editor.trigger.mock.calls[0][1]).toBe('closeFindWidget');
  });

  test('does nothing when the find widget is not open', () => {
    const editor = fakeEditor({ isRevealed: false });
    mount(editor);
    press('Escape');
    expect(editor.trigger).not.toHaveBeenCalled();
  });

  test('ignores every other key', () => {
    const editor = fakeEditor({ isRevealed: true });
    mount(editor);
    for (const key of ['Enter', 'a', 'Tab', 'ArrowDown', 'F3']) { press(key); }
    expect(editor.trigger).not.toHaveBeenCalled();
  });

  test('stands down while a modal dialog is open', () => {
    // Escape belongs to the dialog; reaching past it to close a find bar
    // buried underneath would be a surprise.
    const editor = fakeEditor({ isRevealed: true });
    mount(editor);
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    document.body.appendChild(dialog);

    press('Escape');

    expect(editor.trigger).not.toHaveBeenCalled();
  });

  test('stands down while the settings drawer is open', () => {
    const editor = fakeEditor({ isRevealed: true });
    mount(editor);
    const drawer = document.createElement('div');
    drawer.className = 'settings-drawer is-open';
    document.body.appendChild(drawer);

    press('Escape');

    expect(editor.trigger).not.toHaveBeenCalled();
  });

  test('falls back to the rendered widget when the controller is unavailable', () => {
    // The contribution id is Monaco-internal; a Monaco upgrade that moves it
    // must degrade to "still closes", not "silently stops working".
    const editor = fakeEditor({ controller: false, domOpen: true });
    mount(editor);
    press('Escape');
    expect(editor.trigger).toHaveBeenCalledTimes(1);
  });

  test('falls back to the DOM when reading the controller throws', () => {
    const editor = fakeEditor({ throws: true, domOpen: true });
    mount(editor);
    press('Escape');
    expect(editor.trigger).toHaveBeenCalledTimes(1);
  });

  test('DOM fallback still respects a closed widget', () => {
    const editor = fakeEditor({ controller: false, domOpen: false });
    mount(editor);
    press('Escape');
    expect(editor.trigger).not.toHaveBeenCalled();
  });

  test('no editor mounted yet is a no-op, not a crash', () => {
    const ref = { current: null };
    renderHook(() => useFindWidgetEscape(ref));
    expect(() => press('Escape')).not.toThrow();
  });

  test('an editor without trigger() is a no-op, not a crash', () => {
    const ref = { current: { getDomNode: () => document.createElement('div') } };
    renderHook(() => useFindWidgetEscape(ref));
    expect(() => press('Escape')).not.toThrow();
  });

  test('unmount removes the listener', () => {
    const editor = fakeEditor({ isRevealed: true });
    const { view } = mount(editor);
    view.unmount();
    press('Escape');
    expect(editor.trigger).not.toHaveBeenCalled();
  });
});
