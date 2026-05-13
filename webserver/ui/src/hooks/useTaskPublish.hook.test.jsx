// Tests for useTaskPublish — drives the Push / Pull / PR buttons.
// Contract:
//   - No taskId → all flags false, no polling.
//   - With taskId → fetches publish state, exposes flags.
//   - push/pull/createPullRequest set their busy flag, call api,
//     then refresh.
//   - Concurrent push/pull/PR calls while busy return null (no
//     double-fires).

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  createTaskPullRequest: vi.fn(),
  fetchTaskPublishState: vi.fn(),
  pullTask: vi.fn(),
  pushTask: vi.fn(),
}));

import {
  createTaskPullRequest,
  fetchTaskPublishState,
  pullTask,
  pushTask,
} from '../api.js';
import { useTaskPublish } from './useTaskPublish.js';


beforeEach(() => {
  fetchTaskPublishState.mockReset();
  createTaskPullRequest.mockReset();
  pullTask.mockReset();
  pushTask.mockReset();
});


describe('useTaskPublish — without taskId', () => {

  test('no fetching when taskId is null', () => {
    renderHook(() => useTaskPublish(null));
    expect(fetchTaskPublishState).not.toHaveBeenCalled();
  });

  test('all flags false initially', () => {
    const { result } = renderHook(() => useTaskPublish(null));
    expect(result.current.hasWorkspace).toBe(false);
    expect(result.current.hasChangesToPush).toBe(false);
    expect(result.current.hasPullRequest).toBe(false);
    expect(result.current.pullRequestUrls).toEqual([]);
    expect(result.current.pushBusy).toBe(false);
    expect(result.current.pullBusy).toBe(false);
    expect(result.current.prBusy).toBe(false);
  });

  test('push without taskId returns null without calling api', async () => {
    const { result } = renderHook(() => useTaskPublish(null));
    let out;
    await act(async () => { out = await result.current.push(); });
    expect(out).toBeNull();
    expect(pushTask).not.toHaveBeenCalled();
  });
});


describe('useTaskPublish — with taskId', () => {

  test('fetches state on mount', async () => {
    fetchTaskPublishState.mockResolvedValue({
      has_workspace: true,
      has_changes_to_push: true,
      has_pull_request: false,
      pull_request_urls: [],
    });

    const { result } = renderHook(() => useTaskPublish('T1'));

    await waitFor(() => expect(result.current.hasWorkspace).toBe(true));
    expect(result.current.hasChangesToPush).toBe(true);
    expect(result.current.hasPullRequest).toBe(false);
  });

  test('exposes pull_request_urls (filtered to truthy)', async () => {
    fetchTaskPublishState.mockResolvedValue({
      has_workspace: true,
      has_pull_request: true,
      pull_request_urls: ['https://example/pr/1', '', null, 'https://example/pr/2'],
    });

    const { result } = renderHook(() => useTaskPublish('T1'));
    await waitFor(() => {
      expect(result.current.pullRequestUrls).toEqual([
        'https://example/pr/1', 'https://example/pr/2',
      ]);
    });
  });

  test('polls every 10s', async () => {
    vi.useFakeTimers();
    try {
      fetchTaskPublishState
        .mockResolvedValueOnce({ has_workspace: false })
        .mockResolvedValueOnce({ has_workspace: true });

      const { result } = renderHook(() => useTaskPublish('T1'));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.hasWorkspace).toBe(false);

      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(result.current.hasWorkspace).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  test('fetch errors keep last-known state', async () => {
    vi.useFakeTimers();
    try {
      fetchTaskPublishState
        .mockResolvedValueOnce({ has_workspace: true, has_changes_to_push: true })
        .mockRejectedValueOnce(new Error('network'));

      const { result } = renderHook(() => useTaskPublish('T1'));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.hasWorkspace).toBe(true);

      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      // Last-known state retained.
      expect(result.current.hasWorkspace).toBe(true);
      expect(result.current.hasChangesToPush).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});


describe('useTaskPublish — actions', () => {

  beforeEach(() => {
    fetchTaskPublishState.mockResolvedValue({ has_workspace: true });
  });

  test('push() sets pushBusy=true while in flight', async () => {
    let resolvePush;
    pushTask.mockReturnValue(new Promise((r) => { resolvePush = r; }));

    const { result } = renderHook(() => useTaskPublish('T1'));
    await waitFor(() => expect(result.current.hasWorkspace).toBe(true));

    act(() => { result.current.push(); });
    expect(result.current.pushBusy).toBe(true);

    await act(async () => {
      resolvePush({ ok: true });
      await Promise.resolve();
    });
    expect(result.current.pushBusy).toBe(false);
  });

  test('push() is a no-op while pushBusy=true (double-click guard)', async () => {
    pushTask.mockReturnValue(new Promise(() => {}));  // never resolves

    const { result } = renderHook(() => useTaskPublish('T1'));
    await waitFor(() => expect(result.current.hasWorkspace).toBe(true));

    act(() => { result.current.push(); });
    let secondResult;
    await act(async () => { secondResult = await result.current.push(); });

    expect(pushTask).toHaveBeenCalledTimes(1);
    expect(secondResult).toBeNull();
  });

  test('pull() flow mirrors push()', async () => {
    pullTask.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useTaskPublish('T1'));
    await waitFor(() => expect(result.current.hasWorkspace).toBe(true));

    let out;
    await act(async () => { out = await result.current.pull(); });

    expect(pullTask).toHaveBeenCalledWith('T1');
    expect(out).toEqual({ ok: true });
    expect(result.current.pullBusy).toBe(false);
  });

  test('createPullRequest() flow mirrors push()', async () => {
    createTaskPullRequest.mockResolvedValue({ ok: true, url: 'https://pr/1' });
    const { result } = renderHook(() => useTaskPublish('T1'));
    await waitFor(() => expect(result.current.hasWorkspace).toBe(true));

    let out;
    await act(async () => { out = await result.current.createPullRequest(); });

    expect(createTaskPullRequest).toHaveBeenCalledWith('T1');
    expect(out.url).toBe('https://pr/1');
    expect(result.current.prBusy).toBe(false);
  });

  test('action triggers refresh after completion', async () => {
    pushTask.mockResolvedValue({ ok: true });
    fetchTaskPublishState
      .mockResolvedValueOnce({ has_workspace: true, has_changes_to_push: true })
      .mockResolvedValueOnce({ has_workspace: true, has_changes_to_push: false });

    const { result } = renderHook(() => useTaskPublish('T1'));
    await waitFor(() => expect(result.current.hasChangesToPush).toBe(true));

    await act(async () => { await result.current.push(); });

    await waitFor(() => expect(result.current.hasChangesToPush).toBe(false));
  });
});
