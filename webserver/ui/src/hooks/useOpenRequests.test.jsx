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
    // Switching task re-subscribes: the replay of the last request opens nothing.
    rerender({ taskId: 'UNA-9' });
    expect(open).toHaveBeenCalledTimes(2);
  });
});
