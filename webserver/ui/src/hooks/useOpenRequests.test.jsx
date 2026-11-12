// App opens a tab for each NEW request about the task on screen.
import { describe, test, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { createOpenRequests } from '../stores/pubsub.js';
import { useOpenRequests } from './useOpenRequests.js';

describe('useOpenRequests', () => {
  test('opens each new request for the task on screen, and nothing else', () => {
    const requests = createOpenRequests();
    const open = vi.fn();
    const { rerender } = renderHook(
      ({ taskId }) => useOpenRequests(requests, taskId, open), { initialProps: { taskId: 'UNA-1' } },
    );
    expect(open).not.toHaveBeenCalled();  // nothing asked yet
    act(() => requests.open('UNA-1', { label: 'Plan board' }));
    expect(open).toHaveBeenLastCalledWith('UNA-1', { label: 'Plan board', taskId: 'UNA-1', seq: 1 });
    act(() => requests.open(''));  // '' = the task on screen
    expect(open).toHaveBeenLastCalledWith('UNA-1', { taskId: '', seq: 2 });
    act(() => requests.open('UNA-9'));  // another task's tabs are not showing
    expect(open).toHaveBeenCalledTimes(2);
    // ...but it waits for that task: a row in the tasks menu switches to its
    // task and asks for its view in one click, before the switch renders.
    rerender({ taskId: 'UNA-9' });
    expect(open).toHaveBeenCalledTimes(3);
    expect(open).toHaveBeenLastCalledWith('UNA-9', { taskId: 'UNA-9', seq: 3 });
    // Once: the replay a re-subscribe gets opens nothing more.
    rerender({ taskId: 'UNA-1' });
    rerender({ taskId: 'UNA-9' });
    expect(open).toHaveBeenCalledTimes(3);
  });

  test('a request about a task never switched to in time is dropped', () => {
    const requests = createOpenRequests();
    const open = vi.fn();
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000);
    try {
      const { rerender } = renderHook(
        ({ taskId }) => useOpenRequests(requests, taskId, open), { initialProps: { taskId: 'UNA-1' } },
      );
      act(() => requests.open('UNA-9'));
      now.mockReturnValue(1_000_000 + 60_000);  // a minute later
      rerender({ taskId: 'UNA-9' });
      expect(open).not.toHaveBeenCalled();
    } finally {
      now.mockRestore();
    }
  });
});
