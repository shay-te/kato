// Tests for useSessions — polls /api/sessions every 5s. Drives the
// sidebar tab list. Contract:
//   - Starts with [].
//   - Refresh sets state when fetch returns an array.
//   - Non-array responses are ignored (last good state retained).
//   - Errors are swallowed.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchSessionList: vi.fn(),
}));

import { fetchSessionList } from '../api.js';
import { useSessions } from './useSessions.js';


beforeEach(() => {
  fetchSessionList.mockReset();
});


describe('useSessions', () => {

  test('starts with an empty array', () => {
    fetchSessionList.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useSessions());
    expect(result.current.sessions).toEqual([]);
  });

  test('populates sessions when fetch resolves with an array', async () => {
    fetchSessionList.mockResolvedValue([
      { task_id: 'T1' }, { task_id: 'T2' },
    ]);

    const { result } = renderHook(() => useSessions());
    await waitFor(() => expect(result.current.sessions.length).toBe(2));
    expect(result.current.sessions.map((s) => s.task_id)).toEqual(['T1', 'T2']);
  });

  test('ignores non-array responses (last good state preserved)', async () => {
    vi.useFakeTimers();
    try {
      fetchSessionList
        .mockResolvedValueOnce([{ task_id: 'T1' }])
        .mockResolvedValueOnce({ error: 'oops' });

      const { result } = renderHook(() => useSessions());
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.sessions.length).toBe(1);

      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      // Still one session — the error envelope didn't wipe the list.
      expect(result.current.sessions.length).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  test('swallows fetch errors', async () => {
    fetchSessionList.mockRejectedValue(new Error('502'));
    const { result } = renderHook(() => useSessions());
    await act(async () => { await Promise.resolve(); });
    expect(result.current.sessions).toEqual([]);
  });

  test('polls every 5 seconds', async () => {
    vi.useFakeTimers();
    try {
      fetchSessionList
        .mockResolvedValueOnce([{ task_id: 'T1' }])
        .mockResolvedValueOnce([{ task_id: 'T1' }, { task_id: 'T2' }]);

      const { result } = renderHook(() => useSessions());
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.sessions.length).toBe(1);

      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(result.current.sessions.length).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  test('refresh() is exposed for imperative re-fetch', async () => {
    fetchSessionList.mockResolvedValue([{ task_id: 'T1' }]);
    const { result } = renderHook(() => useSessions());
    await waitFor(() => expect(result.current.sessions.length).toBe(1));

    fetchSessionList.mockResolvedValue([{ task_id: 'T1' }, { task_id: 'T2' }]);
    await act(async () => { await result.current.refresh(); });

    expect(result.current.sessions.length).toBe(2);
  });
});
