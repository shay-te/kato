// useDelayedFlag — a loader that only shows when a wait is real.
//
// "refresh/moving between task have a super fast blinking of the tree moving
// from loading state to loaded state. super annoying." The data usually lands
// within a few frames; a loader shown immediately flashes for exactly those
// frames. These pin the two halves of the contract: fast loads never show it,
// slow ones still do.

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import { useDelayedFlag } from './useDelayedFlag.js';

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

function mount(active, delayMs = 300) {
  return renderHook(
    (props) => useDelayedFlag(props.active, props.delayMs),
    { initialProps: { active, delayMs } },
  );
}

describe('useDelayedFlag', () => {
  test('stays off during the grace period', () => {
    const { result } = mount(true);
    expect(result.current).toBe(false);
    act(() => { vi.advanceTimersByTime(299); });
    expect(result.current).toBe(false);
  });

  test('turns on once the condition has held for the whole delay', () => {
    const { result } = mount(true);
    act(() => { vi.advanceTimersByTime(300); });
    expect(result.current).toBe(true);
  });

  test('a condition that clears inside the grace period never shows at all', () => {
    // The fast load: data arrives before the delay, so no loader is painted.
    const { result, rerender } = mount(true);
    act(() => { vi.advanceTimersByTime(120); });
    rerender({ active: false, delayMs: 300 });
    act(() => { vi.advanceTimersByTime(1000); });
    expect(result.current).toBe(false);
  });

  test('turns off in the same render the condition clears', () => {
    const { result, rerender } = mount(true);
    act(() => { vi.advanceTimersByTime(300); });
    expect(result.current).toBe(true);
    rerender({ active: false, delayMs: 300 });
    expect(result.current).toBe(false);
  });

  test('a second wait starts its grace period from scratch', () => {
    const { result, rerender } = mount(true);
    act(() => { vi.advanceTimersByTime(300); });
    rerender({ active: false, delayMs: 300 });
    rerender({ active: true, delayMs: 300 });
    expect(result.current).toBe(false);
    act(() => { vi.advanceTimersByTime(300); });
    expect(result.current).toBe(true);
  });

  test('an inactive condition is off and schedules nothing', () => {
    const { result } = mount(false);
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current).toBe(false);
  });

  test('a zero delay turns on after the tick', () => {
    const { result } = mount(true, 0);
    act(() => { vi.advanceTimersByTime(0); });
    expect(result.current).toBe(true);
  });
});
