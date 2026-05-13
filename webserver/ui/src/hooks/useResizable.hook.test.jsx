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
