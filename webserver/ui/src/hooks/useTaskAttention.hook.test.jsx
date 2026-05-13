// Tests for the tiny attention-set hook. The contract:
//   - mark/clear are idempotent (no-op when state would not change).
//   - Set identity changes ONLY when the set's contents change
//     (matters for React memoization downstream).
//   - Empty / null task ids are ignored.

import { describe, test, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useTaskAttention } from './useTaskAttention.js';


describe('useTaskAttention', () => {

  test('starts with an empty Set', () => {
    const { result } = renderHook(() => useTaskAttention());
    expect(result.current.taskIds).toBeInstanceOf(Set);
    expect(result.current.taskIds.size).toBe(0);
  });

  test('mark adds a task id', () => {
    const { result } = renderHook(() => useTaskAttention());
    act(() => { result.current.mark('T1'); });
    expect(result.current.taskIds.has('T1')).toBe(true);
  });

  test('mark is idempotent — re-marking does not change the Set identity', () => {
    // Critical for React: if downstream components useMemo on
    // taskIds, a fresh Set on every mark would invalidate caches.
    const { result } = renderHook(() => useTaskAttention());
    act(() => { result.current.mark('T1'); });
    const firstSet = result.current.taskIds;
    act(() => { result.current.mark('T1'); });
    expect(result.current.taskIds).toBe(firstSet);
  });

  test('mark with empty / null id is a no-op', () => {
    const { result } = renderHook(() => useTaskAttention());
    act(() => { result.current.mark(''); });
    act(() => { result.current.mark(null); });
    act(() => { result.current.mark(undefined); });
    expect(result.current.taskIds.size).toBe(0);
  });

  test('clear removes a marked task id', () => {
    const { result } = renderHook(() => useTaskAttention());
    act(() => { result.current.mark('T1'); });
    act(() => { result.current.clear('T1'); });
    expect(result.current.taskIds.has('T1')).toBe(false);
  });

  test('clear is idempotent — clearing absent id keeps Set identity', () => {
    const { result } = renderHook(() => useTaskAttention());
    const firstSet = result.current.taskIds;
    act(() => { result.current.clear('never-marked'); });
    expect(result.current.taskIds).toBe(firstSet);
  });

  test('clear with empty / null id is a no-op', () => {
    const { result } = renderHook(() => useTaskAttention());
    act(() => { result.current.mark('T1'); });
    const before = result.current.taskIds;
    act(() => { result.current.clear(''); });
    act(() => { result.current.clear(null); });
    expect(result.current.taskIds).toBe(before);
    expect(result.current.taskIds.has('T1')).toBe(true);
  });

  test('multiple task ids: marks + clears coexist independently', () => {
    const { result } = renderHook(() => useTaskAttention());
    act(() => { result.current.mark('T1'); });
    act(() => { result.current.mark('T2'); });
    act(() => { result.current.mark('T3'); });
    expect(result.current.taskIds.size).toBe(3);

    act(() => { result.current.clear('T2'); });
    expect(Array.from(result.current.taskIds).sort()).toEqual(['T1', 'T3']);
  });

  test('mark/clear callbacks are stable across renders', () => {
    // useCallback with empty deps — the parent component can pass
    // them down without churning child memos.
    const { result, rerender } = renderHook(() => useTaskAttention());
    const firstMark = result.current.mark;
    const firstClear = result.current.clear;
    rerender();
    expect(result.current.mark).toBe(firstMark);
    expect(result.current.clear).toBe(firstClear);
  });
});
