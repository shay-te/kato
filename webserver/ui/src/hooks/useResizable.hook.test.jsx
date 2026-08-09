// Tests for the ``useResizable`` hook. The underlying storage
// helpers (resizableStorage.js) have their own unit tests; this
// file proves the React wiring:
//
//   - Hydrates from localStorage on mount when a value exists.
//   - Falls back to defaultWidth when no value or value is malformed.
//   - Clamps the hydrated value to [minWidth, maxWidth].
//   - Persists width changes to localStorage.
//   - onPointerDown wires up the move + up handlers and drives width
//     through the clamp on each move.

import { describe, test, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useResizable } from './useResizable.js';


const DEFAULTS = {
  storageKey: 'kato.pane.test',
  defaultWidth: 300,
  minWidth: 200,
  maxWidth: 600,
};


describe('useResizable — hydration', () => {

  test('uses defaultWidth when no persisted value', () => {
    const { result } = renderHook(() => useResizable(DEFAULTS));
    expect(result.current.width).toBe(300);
  });

  test('hydrates from localStorage when a value exists', () => {
    window.localStorage.setItem('kato.pane.test', '420');
    const { result } = renderHook(() => useResizable(DEFAULTS));
    expect(result.current.width).toBe(420);
  });

  test('clamps a too-small persisted value to minWidth', () => {
    window.localStorage.setItem('kato.pane.test', '50');
    const { result } = renderHook(() => useResizable(DEFAULTS));
    expect(result.current.width).toBe(200);
  });

  test('clamps a too-large persisted value to maxWidth', () => {
    window.localStorage.setItem('kato.pane.test', '9999');
    const { result } = renderHook(() => useResizable(DEFAULTS));
    expect(result.current.width).toBe(600);
  });

  test('falls back to defaultWidth on non-numeric garbage', () => {
    window.localStorage.setItem('kato.pane.test', 'not-a-number');
    const { result } = renderHook(() => useResizable(DEFAULTS));
    expect(result.current.width).toBe(300);
  });

  test('falls back to defaultWidth on empty string', () => {
    window.localStorage.setItem('kato.pane.test', '');
    const { result } = renderHook(() => useResizable(DEFAULTS));
    expect(result.current.width).toBe(300);
  });
});


describe('useResizable — persistence on width change', () => {

  test('writes width to localStorage after a drag', () => {
    const { result } = renderHook(() => useResizable(DEFAULTS));

    // Simulate a pointer-driven drag.
    act(() => {
      result.current.onPointerDown({
        preventDefault: () => {},
        clientX: 100,
      });
    });

    // Trigger a mousemove that nudges width by -50 (anchor=right).
    act(() => {
      document.dispatchEvent(new MouseEvent('mousemove', { clientX: 150 }));
    });

    act(() => {
      document.dispatchEvent(new MouseEvent('mouseup'));
    });

    // Persisted to localStorage with the new value.
    const persisted = window.localStorage.getItem('kato.pane.test');
    expect(persisted).not.toBe('300');  // changed from default
    // Width should be 300 - 50 = 250 (anchor=right inverts).
    expect(result.current.width).toBe(250);
    expect(persisted).toBe('250');
  });

  test('anchor=left drags in the opposite direction', () => {
    const { result } = renderHook(() => useResizable({
      ...DEFAULTS, anchor: 'left',
    }));
    act(() => {
      result.current.onPointerDown({
        preventDefault: () => {},
        clientX: 100,
      });
    });
    act(() => {
      document.dispatchEvent(new MouseEvent('mousemove', { clientX: 150 }));
    });
    act(() => {
      document.dispatchEvent(new MouseEvent('mouseup'));
    });
    // anchor=left: width grows when clientX grows. 300 + 50 = 350.
    expect(result.current.width).toBe(350);
  });
});


describe('useResizable — clamping during drag', () => {

  test('drag past maxWidth clamps to maxWidth', () => {
    const { result } = renderHook(() => useResizable(DEFAULTS));
    act(() => {
      result.current.onPointerDown({
        preventDefault: () => {},
        clientX: 1000,
      });
    });
    // Huge negative dx (anchor=right inverts so this GROWS width).
    act(() => {
      document.dispatchEvent(new MouseEvent('mousemove', { clientX: -5000 }));
    });
    act(() => {
      document.dispatchEvent(new MouseEvent('mouseup'));
    });
    expect(result.current.width).toBe(600);  // clamped at max
  });

  test('drag past minWidth clamps to minWidth', () => {
    const { result } = renderHook(() => useResizable(DEFAULTS));
    act(() => {
      result.current.onPointerDown({
        preventDefault: () => {},
        clientX: 0,
      });
    });
    // Huge positive dx (anchor=right inverts so this SHRINKS width).
    act(() => {
      document.dispatchEvent(new MouseEvent('mousemove', { clientX: 5000 }));
    });
    act(() => {
      document.dispatchEvent(new MouseEvent('mouseup'));
    });
    expect(result.current.width).toBe(200);  // clamped at min
  });

  test('re-clamps the width down when maxWidth shrinks (viewport got narrower)', () => {
    // The chat pane's maxWidth is dynamic = viewport − centre-min. When the
    // window narrows, the stored width must drop to the new max so the centre
    // pane is never squeezed below its minimum — without waiting for a drag.
    window.localStorage.removeItem('kato.pane.dynamic');
    const cfg = {
      storageKey: 'kato.pane.dynamic', defaultWidth: 500, minWidth: 200,
    };
    const { result, rerender } = renderHook(
      ({ maxWidth }) => useResizable({ ...cfg, maxWidth }),
      { initialProps: { maxWidth: 900 } },
    );
    expect(result.current.width).toBe(500);   // within [200, 900]
    rerender({ maxWidth: 350 });               // viewport shrank
    expect(result.current.width).toBe(350);    // re-clamped down, no drag
  });
});


describe('useResizable — pointer-down side effects', () => {

  test('adds the kato-resizing class to body during a drag', () => {
    const { result } = renderHook(() => useResizable(DEFAULTS));
    act(() => {
      result.current.onPointerDown({
        preventDefault: () => {},
        clientX: 50,
      });
    });
    expect(document.body.classList.contains('kato-resizing')).toBe(true);
  });

  test('removes the kato-resizing class on mouseup', () => {
    const { result } = renderHook(() => useResizable(DEFAULTS));
    act(() => {
      result.current.onPointerDown({
        preventDefault: () => {},
        clientX: 50,
      });
    });
    act(() => { document.dispatchEvent(new MouseEvent('mouseup')); });
    expect(document.body.classList.contains('kato-resizing')).toBe(false);
  });

  test('calls preventDefault on the pointer-down event', () => {
    // Avoids text-selection during resize.
    const preventDefault = vi.fn();
    const { result } = renderHook(() => useResizable(DEFAULTS));
    act(() => {
      result.current.onPointerDown({ preventDefault, clientX: 50 });
    });
    expect(preventDefault).toHaveBeenCalled();
    // Cleanup so the next test doesn't see stale listeners.
    act(() => { document.dispatchEvent(new MouseEvent('mouseup')); });
  });
});


// Two classes, two scopes. ``kato-resizing`` (above) is GLOBAL and owns
// document-wide ergonomics only — cursor + selection lock. The blue
// active paint hangs off ``is-dragging`` on the ONE handle under the
// pointer. Regression: the paint used to key off the body class, so
// dragging the files/editor boundary also lit the editor/chat one and it
// read as though both boundaries were moving.
describe('useResizable — per-handle drag marking', () => {

  function mountHandle() {
    const handle = document.createElement('div');
    handle.className = 'pane-resizer';
    document.body.appendChild(handle);
    return handle;
  }

  test('marks only the handle the drag started on', () => {
    const dragged = mountHandle();
    const other = mountHandle();
    const { result } = renderHook(() => useResizable(DEFAULTS));

    act(() => {
      result.current.onPointerDown({
        preventDefault: () => {},
        clientX: 50,
        currentTarget: dragged,
      });
    });

    expect(dragged.classList.contains('is-dragging')).toBe(true);
    expect(other.classList.contains('is-dragging')).toBe(false);

    act(() => { document.dispatchEvent(new MouseEvent('mouseup')); });
    dragged.remove();
    other.remove();
  });

  test('clears the mark on mouseup', () => {
    const handle = mountHandle();
    const { result } = renderHook(() => useResizable(DEFAULTS));

    act(() => {
      result.current.onPointerDown({
        preventDefault: () => {},
        clientX: 50,
        currentTarget: handle,
      });
    });
    act(() => { document.dispatchEvent(new MouseEvent('mouseup')); });

    expect(handle.classList.contains('is-dragging')).toBe(false);
    handle.remove();
  });
});
