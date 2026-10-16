// Tests for useUsage — the Usage dialog's one read of the agent transcripts.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchUsage: vi.fn(),
}));

import { fetchUsage } from '../api.js';
import { useUsage } from './useUsage.js';

beforeEach(() => {
  fetchUsage.mockReset();
});

describe('useUsage', () => {
  test('reads once on mount, loading until the answer lands', async () => {
    fetchUsage.mockResolvedValue({ tasks: [], totals: {} });
    const { result } = renderHook(() => useUsage());
    expect(result.current.status).toBe('loading');
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.data).toEqual({ tasks: [], totals: {} });
    expect(fetchUsage).toHaveBeenCalledTimes(1);
  });

  test('a failed read reports the reason and keeps nothing stale', async () => {
    fetchUsage.mockRejectedValue(new Error('504 gateway'));
    const { result } = renderHook(() => useUsage());
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.error).toBe('504 gateway');
    expect(result.current.data).toBeNull();
  });

  test('reload keeps the last figures while it re-reads, then swaps them', async () => {
    fetchUsage.mockResolvedValueOnce({ tasks: [{ task_id: 'A' }] });
    const { result } = renderHook(() => useUsage());
    await waitFor(() => expect(result.current.status).toBe('ready'));

    let finish;
    fetchUsage.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    act(() => { result.current.reload(); });
    expect(result.current.status).toBe('refreshing');
    expect(result.current.data.tasks[0].task_id).toBe('A');

    await act(async () => { finish({ tasks: [{ task_id: 'B' }] }); });
    expect(result.current.status).toBe('ready');
    expect(result.current.data.tasks[0].task_id).toBe('B');
  });

  test('a slow older answer never overwrites a newer one', async () => {
    let finishFirst;
    fetchUsage.mockReturnValueOnce(new Promise((resolve) => { finishFirst = resolve; }));
    const { result } = renderHook(() => useUsage());

    fetchUsage.mockResolvedValueOnce({ tasks: [{ task_id: 'NEW' }] });
    await act(async () => { await result.current.reload(); });
    expect(result.current.data.tasks[0].task_id).toBe('NEW');

    await act(async () => { finishFirst({ tasks: [{ task_id: 'OLD' }] }); });
    expect(result.current.data.tasks[0].task_id).toBe('NEW');
  });

  test('an answer that arrives after the dialog closed is dropped', async () => {
    let finish;
    fetchUsage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const { result, unmount } = renderHook(() => useUsage());
    unmount();
    await act(async () => { finish({ tasks: [] }); });
    expect(result.current.status).toBe('loading');
  });

  test('a failed re-read keeps the figures already shown', async () => {
    fetchUsage.mockResolvedValueOnce({ tasks: [{ task_id: 'A' }] });
    const { result } = renderHook(() => useUsage());
    await waitFor(() => expect(result.current.status).toBe('ready'));

    fetchUsage.mockRejectedValueOnce(new Error('boom'));
    await act(async () => { await result.current.reload(); });

    expect(result.current.status).toBe('error');
    expect(result.current.data.tasks[0].task_id).toBe('A');
  });
});
