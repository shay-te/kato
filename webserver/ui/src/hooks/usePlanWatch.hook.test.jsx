// Tests for usePlanWatch — polls the active task's plan.md and fires
// ``onFreshPlan`` ONLY when a strictly-newer plan appears (after a
// baseline is established), so switching to a task with an existing plan
// never yanks the centre pane.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchSessionPlan: vi.fn(),
}));

import { fetchSessionPlan } from '../api.js';
import { usePlanWatch } from './usePlanWatch.js';

beforeEach(() => {
  fetchSessionPlan.mockReset();
});

describe('usePlanWatch', () => {
  test('exposes plan content + availability for the task', async () => {
    vi.useFakeTimers();
    try {
      fetchSessionPlan.mockResolvedValue(
        { exists: true, content: '# Plan', mtime: 10 });
      const { result } = renderHook(() => usePlanWatch('T1', () => {}));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.content).toBe('# Plan');
      expect(result.current.available).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  test('does NOT auto-open on the first observation of an existing plan', async () => {
    vi.useFakeTimers();
    try {
      const onFresh = vi.fn();
      fetchSessionPlan.mockResolvedValue(
        { exists: true, content: '# Plan', mtime: 10 });
      renderHook(() => usePlanWatch('T1', onFresh));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      // First tick = baseline only.
      expect(onFresh).not.toHaveBeenCalled();
      // Second tick, same mtime → still no open.
      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
      expect(onFresh).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  test('auto-opens when a strictly-newer plan appears', async () => {
    vi.useFakeTimers();
    try {
      const onFresh = vi.fn();
      fetchSessionPlan
        .mockResolvedValueOnce({ exists: false, content: '', mtime: 0 })
        .mockResolvedValue({ exists: true, content: '# Fresh', mtime: 20 });
      renderHook(() => usePlanWatch('T1', onFresh));
      // First tick: no plan → baseline 0.
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(onFresh).not.toHaveBeenCalled();
      // Next tick: a new plan lands (mtime 20 > 0) → auto-open once.
      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
      expect(onFresh).toHaveBeenCalledTimes(1);
      expect(onFresh).toHaveBeenCalledWith('T1');
    } finally {
      vi.useRealTimers();
    }
  });

  test('does not poll when there is no task', async () => {
    renderHook(() => usePlanWatch('', () => {}));
    await act(async () => { await Promise.resolve(); });
    expect(fetchSessionPlan).not.toHaveBeenCalled();
  });
});
