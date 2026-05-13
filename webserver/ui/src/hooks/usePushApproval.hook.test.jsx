// Tests for usePushApproval — polls /api/awaiting-push and exposes
// an approve() action. Contract:
//   - No taskId → awaiting=false, no polling.
//   - Polls every 5s while taskId is set.
//   - approve() posts to the api, optimistically clears awaiting
//     on success, retains awaiting on failure.
//   - Double-click guard via the busy flag.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  approveTaskPush: vi.fn(),
  fetchAwaitingPushApproval: vi.fn(),
}));

import { approveTaskPush, fetchAwaitingPushApproval } from '../api.js';
import { usePushApproval } from './usePushApproval.js';


beforeEach(() => {
  approveTaskPush.mockReset();
  fetchAwaitingPushApproval.mockReset();
});


describe('usePushApproval — without taskId', () => {

  test('no fetch, awaiting=false', () => {
    renderHook(() => usePushApproval(null));
    expect(fetchAwaitingPushApproval).not.toHaveBeenCalled();
  });

  test('approve() without taskId returns null', async () => {
    const { result } = renderHook(() => usePushApproval(null));
    let out;
    await act(async () => { out = await result.current.approve(); });
    expect(out).toBeNull();
    expect(approveTaskPush).not.toHaveBeenCalled();
  });
});


describe('usePushApproval — with taskId', () => {

  test('fetches awaiting state on mount', async () => {
    fetchAwaitingPushApproval.mockResolvedValue({ awaiting_push_approval: true });
    const { result } = renderHook(() => usePushApproval('T1'));

    await waitFor(() => expect(result.current.awaiting).toBe(true));
  });

  test('polls every 5s', async () => {
    vi.useFakeTimers();
    try {
      fetchAwaitingPushApproval
        .mockResolvedValueOnce({ awaiting_push_approval: false })
        .mockResolvedValueOnce({ awaiting_push_approval: true });

      const { result } = renderHook(() => usePushApproval('T1'));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.awaiting).toBe(false);

      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(result.current.awaiting).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  test('fetch errors keep last-known state', async () => {
    vi.useFakeTimers();
    try {
      fetchAwaitingPushApproval
        .mockResolvedValueOnce({ awaiting_push_approval: true })
        .mockRejectedValueOnce(new Error('502'));

      const { result } = renderHook(() => usePushApproval('T1'));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.awaiting).toBe(true);

      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(result.current.awaiting).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  test('taskId change resets to awaiting=false until next fetch', async () => {
    fetchAwaitingPushApproval
      .mockResolvedValueOnce({ awaiting_push_approval: true })
      .mockResolvedValueOnce({ awaiting_push_approval: false });

    const { result, rerender } = renderHook(
      ({ id }) => usePushApproval(id),
      { initialProps: { id: 'T1' } },
    );
    await waitFor(() => expect(result.current.awaiting).toBe(true));

    rerender({ id: 'T2' });
    // While the new fetch is in flight, the hook starts the new
    // taskId fresh. After the second resolve, awaiting=false.
    await waitFor(() => expect(result.current.awaiting).toBe(false));
  });
});


describe('usePushApproval — approve action', () => {

  beforeEach(() => {
    fetchAwaitingPushApproval.mockResolvedValue({ awaiting_push_approval: true });
  });

  test('approve() sets busy=true while in flight', async () => {
    let resolveApprove;
    approveTaskPush.mockReturnValue(new Promise((r) => { resolveApprove = r; }));

    const { result } = renderHook(() => usePushApproval('T1'));
    await waitFor(() => expect(result.current.awaiting).toBe(true));

    act(() => { result.current.approve(); });
    expect(result.current.busy).toBe(true);

    await act(async () => {
      resolveApprove({ ok: true });
      await Promise.resolve();
    });
    expect(result.current.busy).toBe(false);
  });

  test('approve() success: optimistically clears awaiting locally', async () => {
    approveTaskPush.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => usePushApproval('T1'));
    await waitFor(() => expect(result.current.awaiting).toBe(true));

    await act(async () => { await result.current.approve(); });

    // Local state flips immediately (no need to wait for the next
    // poll) so the operator sees the action took effect.
    expect(result.current.awaiting).toBe(false);
  });

  test('approve() failure: awaiting stays true (operator can retry)', async () => {
    approveTaskPush.mockResolvedValue({ ok: false, error: 'workspace busy' });
    const { result } = renderHook(() => usePushApproval('T1'));
    await waitFor(() => expect(result.current.awaiting).toBe(true));

    await act(async () => { await result.current.approve(); });

    expect(result.current.awaiting).toBe(true);
  });

  test('double-click guard: approve while busy returns null', async () => {
    approveTaskPush.mockReturnValue(new Promise(() => {}));  // never resolves
    const { result } = renderHook(() => usePushApproval('T1'));
    await waitFor(() => expect(result.current.awaiting).toBe(true));

    act(() => { result.current.approve(); });
    let secondResult;
    await act(async () => { secondResult = await result.current.approve(); });

    expect(approveTaskPush).toHaveBeenCalledTimes(1);
    expect(secondResult).toBeNull();
  });
});
