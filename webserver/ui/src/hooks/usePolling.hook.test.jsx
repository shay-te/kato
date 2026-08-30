// usePolling — the loop behind five hooks (sessions, config-status, plan,
// push-approval, safety) plus the permissions settings panel.
//
// It had no tests, and it had drifted from the shared ``createPoller`` it was
// a copy of: no ``document.hidden`` check and a ``setInterval`` that could
// stack overlapping ticks. A backgrounded kato window therefore kept issuing
// ~64 requests a minute with nobody looking at the answers — and each
// ``/api/sessions`` tick runs a full live-session walk plus permission
// auto-resolve on the server, so those were not free reads.
//
// These pin the visibility contract in both directions: quiet while hidden,
// and instantly fresh on return.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { usePolling } from './usePolling.js';

let hidden = false;

function setHidden(next) {
  hidden = next;
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  vi.useFakeTimers();
  hidden = false;
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    get: () => hidden,
  });
});

afterEach(() => {
  vi.useRealTimers();
  delete document.hidden;
});

describe('usePolling', () => {
  test('reads once immediately, then on the interval', () => {
    const fn = vi.fn();
    renderHook(() => usePolling(fn, 1000));
    expect(fn).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1000);
    expect(fn).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(2000);
    expect(fn).toHaveBeenCalledTimes(4);
  });

  test('goes quiet while the tab is hidden', () => {
    // THE FIX. Before this, a backgrounded window polled forever.
    const fn = vi.fn();
    renderHook(() => usePolling(fn, 1000));
    expect(fn).toHaveBeenCalledTimes(1);

    hidden = true;
    vi.advanceTimersByTime(10_000);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  test('resumes on its own once the tab is visible again', () => {
    const fn = vi.fn();
    renderHook(() => usePolling(fn, 1000));
    hidden = true;
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(1);

    hidden = false;
    vi.advanceTimersByTime(1000);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  test('catches up IMMEDIATELY when the operator returns to the tab', () => {
    // A poller that goes quiet must not also leave the view stale for up to a
    // full interval at the exact moment someone looks at it.
    const fn = vi.fn();
    renderHook(() => usePolling(fn, 5000));
    expect(fn).toHaveBeenCalledTimes(1);

    hidden = true;
    vi.advanceTimersByTime(30_000);
    expect(fn).toHaveBeenCalledTimes(1);

    setHidden(false);
    expect(fn).toHaveBeenCalledTimes(2); // no timer advance needed
  });

  test('a visibilitychange that leaves the tab hidden does not fetch', () => {
    const fn = vi.fn();
    renderHook(() => usePolling(fn, 1000));
    fn.mockClear();

    setHidden(true);
    expect(fn).not.toHaveBeenCalled();
  });

  test('enabled:false never polls and never listens', () => {
    const fn = vi.fn();
    renderHook(() => usePolling(fn, 1000, [], { enabled: false }));
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(5000);
    setHidden(true);
    setHidden(false);
    expect(fn).not.toHaveBeenCalled();
  });

  test('unmount stops the loop AND unsubscribes from visibilitychange', () => {
    // A leaked listener would keep firing requests for a hook that is gone —
    // the same class of bug as the leaked interval, just harder to see.
    const fn = vi.fn();
    const { unmount } = renderHook(() => usePolling(fn, 1000));
    fn.mockClear();

    unmount();
    vi.advanceTimersByTime(10_000);
    hidden = true;
    setHidden(false);
    expect(fn).not.toHaveBeenCalled();
  });

  test('a deps change restarts the loop with the new callback', () => {
    const fn = vi.fn();
    const { rerender } = renderHook(({ id }) => usePolling(() => fn(id), 1000, [id]), {
      initialProps: { id: 'A' },
    });
    expect(fn).toHaveBeenLastCalledWith('A');

    rerender({ id: 'B' });
    expect(fn).toHaveBeenLastCalledWith('B'); // immediate, not one interval late

    vi.advanceTimersByTime(1000);
    expect(fn).toHaveBeenLastCalledWith('B');
  });

  test('an unstable inline callback does not restart the loop', () => {
    // fn is read through a ref; re-rendering with a fresh closure must not
    // reset the cadence, or a busy parent would starve the poll entirely.
    const fn = vi.fn();
    const { rerender } = renderHook(() => usePolling(() => fn(), 1000));
    expect(fn).toHaveBeenCalledTimes(1);

    rerender();
    rerender();
    expect(fn).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1000);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
