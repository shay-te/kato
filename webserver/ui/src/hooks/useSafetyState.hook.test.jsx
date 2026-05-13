// Tests for useSafetyState — fetches the safety endpoint on mount,
// re-polls every 30s. Contract:
//   - Initial state is null until the first fetch resolves.
//   - Successful fetch updates state.
//   - Errors are swallowed (defensive — banner shouldn't break the UI).
//   - Unmount cancels in-flight + clears the interval.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchSafetyState: vi.fn(),
}));

import { fetchSafetyState } from '../api.js';
import { useSafetyState } from './useSafetyState.js';


beforeEach(() => {
  // Real timers + short await-flush; fake timers deadlocked with
  // RTL waitFor because waitFor's internal poll uses setTimeout
  // which the mock froze. Real timers + microtask flushing keeps
  // tests fast and correct.
  fetchSafetyState.mockReset();
});


describe('useSafetyState', () => {

  test('starts as null before the first fetch resolves', () => {
    fetchSafetyState.mockReturnValue(new Promise(() => {}));  // never resolves
    const { result } = renderHook(() => useSafetyState());
    expect(result.current).toBeNull();
  });

  test('updates state when fetch resolves', async () => {
    fetchSafetyState.mockResolvedValue({ docker_mode_on: true, gvisor: 'available' });

    const { result } = renderHook(() => useSafetyState());

    await waitFor(() => {
      expect(result.current).toEqual({ docker_mode_on: true, gvisor: 'available' });
    });
  });

  test('swallows fetch errors (banner is best-effort)', async () => {
    fetchSafetyState.mockRejectedValue(new Error('network down'));

    const { result } = renderHook(() => useSafetyState());

    // Let the promise reject — state should stay null, no crash.
    await act(async () => { await Promise.resolve(); });
    expect(result.current).toBeNull();
  });

  test('re-polls on the 30s interval', async () => {
    // Use fake timers ONLY for this test where we need to advance
    // through the 30s interval. ``advanceTimersByTimeAsync`` flushes
    // microtasks so the fetch resolution is observable.
    vi.useFakeTimers();
    try {
      fetchSafetyState
        .mockResolvedValueOnce({ tick: 1 })
        .mockResolvedValueOnce({ tick: 2 });

      const { result } = renderHook(() => useSafetyState());

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(result.current).toEqual({ tick: 1 });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(result.current).toEqual({ tick: 2 });
    } finally {
      vi.useRealTimers();
    }
  });

  test('unmount stops further polling (no after-unmount state set)', async () => {
    vi.useFakeTimers();
    try {
      fetchSafetyState.mockResolvedValue({ docker_mode_on: false });

      const { unmount } = renderHook(() => useSafetyState());
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });

      fetchSafetyState.mockClear();
      unmount();

      // Advance well past the interval — no further fetches should
      // fire because the cleanup function cleared the interval AND
      // set the cancelled flag.
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });

      expect(fetchSafetyState).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
