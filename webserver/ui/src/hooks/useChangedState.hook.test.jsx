// Tests for useChangedState: a poll that found nothing new does nothing.

import { describe, test, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import { useChangedState } from './useChangedState.js';

function counted(initial) {
  let renders = 0;
  const hook = renderHook(() => {
    renders += 1;
    return useChangedState(initial);
  });
  return { hook, renders: () => renders };
}

describe('useChangedState', () => {
  test('starts with the initial value', () => {
    expect(counted({ a: 1 }).hook.result.current[0]).toEqual({ a: 1 });
  });

  test('a new object with the same contents renders nothing', () => {
    const { hook, renders } = counted({ a: 1 });
    const before = renders();
    const held = hook.result.current[0];
    act(() => hook.result.current[1]({ a: 1 }));
    expect(renders()).toBe(before);
    expect(hook.result.current[0]).toBe(held);
  });

  test('a real change is applied', () => {
    const { hook } = counted({ a: 1 });
    act(() => hook.result.current[1]({ a: 2 }));
    expect(hook.result.current[0]).toEqual({ a: 2 });
  });

  test('it compares with what it holds now, not with the start', () => {
    const { hook } = counted({ a: 1 });
    act(() => hook.result.current[1]({ a: 2 }));
    act(() => hook.result.current[1]({ a: 1 }));
    expect(hook.result.current[0]).toEqual({ a: 1 });
  });

  test('the setter never changes, so it is safe in a dependency list', () => {
    const { hook } = counted(null);
    const setter = hook.result.current[1];
    act(() => hook.result.current[1]({ a: 1 }));
    expect(hook.result.current[1]).toBe(setter);
  });
});
