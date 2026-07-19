// Tests for useTaskPublish — drives the Push / Pull / PR buttons.
// Contract:
//   - No taskId → all flags false, no fetching.
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
  fetchTaskPullRequestState: vi.fn(),
  pullTask: vi.fn(),
  pushTask: vi.fn(),
}));

vi.mock('../stores/toastStore.js', () => ({ toastResult: vi.fn() }));

import {
  createTaskPullRequest,
  fetchTaskPublishState,
  fetchTaskPullRequestState,
  pullTask,
  pushTask,
} from '../api.js';
import { toastResult } from '../stores/toastStore.js';
import { useTaskPublish } from './useTaskPublish.js';


beforeEach(() => {
  fetchTaskPublishState.mockReset();
  fetchTaskPullRequestState.mockReset();
  // Benign PR-state default so tests that only care about the git buttons
  // (publish-state) don't have to mock the SEPARATE PR fetch.
  fetchTaskPullRequestState.mockResolvedValue({
    has_pull_request: false, pull_request_urls: [],
  });
  createTaskPullRequest.mockReset();
  pullTask.mockReset();
  pushTask.mockReset();
  toastResult.mockReset();
});


describe('useTaskPublish — push notification', () => {

  // ``/push`` returns the FLAT push payload (``pushed`` is a bool; the
  // repo lists are top-level). The toast's kind comes straight from
  // ``formatPushResult`` — the hook must NOT re-derive it (the old
  // re-derivation read ``body.pushed`` as a dict → every push was blue
  // "info / no action").
  test('a successful push fires a green confirmation toast naming the branch', async () => {
    fetchTaskPublishState.mockResolvedValue({});
    pushTask.mockResolvedValue({
      ok: true,
      body: {
        pushed: true, branch: 'T1',
        pushed_repositories: ['client'],
        skipped_repositories: [], failed_repositories: [],
      },
    });
    const { result } = renderHook(() => useTaskPublish('T1'));
    await act(async () => { await result.current.push(); });
    expect(toastResult).toHaveBeenCalledTimes(1);
    const arg = toastResult.mock.calls[0][0];
    expect(arg.kind).toBe('success');
    expect(arg.title).toContain('Pushed');
    expect(arg.message).toContain('branch T1');
  });

  test('a no-op push toasts blue "Nothing to push" (not a misleading green "Pushed")', async () => {
    fetchTaskPublishState.mockResolvedValue({});
    pushTask.mockResolvedValue({
      ok: true,
      body: {
        pushed: false, branch: 'T1',
        pushed_repositories: [],
        skipped_repositories: [{ repository_id: 'client', reason: 'nothing to push' }],
        failed_repositories: [],
      },
    });
    const { result } = renderHook(() => useTaskPublish('T1'));
    await act(async () => { await result.current.push(); });
    const arg = toastResult.mock.calls[0][0];
    expect(arg.kind).toBe('info');
    expect(arg.title).toContain('Nothing to push');
  });

  test('a failed push toasts an error', async () => {
    fetchTaskPublishState.mockResolvedValue({});
    pushTask.mockResolvedValue({ ok: false, error: 'remote rejected' });
    const { result } = renderHook(() => useTaskPublish('T1'));
    await act(async () => { await result.current.push(); });
    expect(toastResult.mock.calls[0][0].kind).toBe('error');
  });
});


describe('useTaskPublish — without taskId', () => {

  test('no fetching when taskId is null', () => {
    renderHook(() => useTaskPublish(null));
    expect(fetchTaskPublishState).not.toHaveBeenCalled();
    expect(fetchTaskPullRequestState).not.toHaveBeenCalled();
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
    });

    const { result } = renderHook(() => useTaskPublish('T1'));

    await waitFor(() => expect(result.current.hasWorkspace).toBe(true));
    expect(result.current.hasChangesToPush).toBe(true);
    expect(result.current.hasPullRequest).toBe(false);  // from the PR fetch (default)
  });

  test('a failing PR fetch never disables the git buttons', async () => {
    // The PR check is a SEPARATE, best-effort fetch. If it fails (slow
    // provider / 429) the git buttons must stay ready — publishStateError
    // stays false and hasWorkspace stays true.
    fetchTaskPublishState.mockResolvedValue({ has_workspace: true });
    fetchTaskPullRequestState.mockRejectedValue(new Error('429'));

    const { result } = renderHook(() => useTaskPublish('T1'));

    await waitFor(() => expect(result.current.publishStateReady).toBe(true));
    expect(result.current.hasWorkspace).toBe(true);
    expect(result.current.publishStateError).toBe(false);  // PR failure ignored
    expect(result.current.hasPullRequest).toBe(false);
  });

  test('publishStateReady flips true only AFTER a successful fetch', async () => {
    // Distinguishes "haven't checked yet" from a real "no workspace" so the
    // UI never claims "no workspace" during the initial load.
    fetchTaskPublishState.mockResolvedValue({ has_workspace: true });
    const { result } = renderHook(() => useTaskPublish('T1'));
    expect(result.current.publishStateReady).toBe(false);  // before the fetch returns
    await waitFor(() => expect(result.current.publishStateReady).toBe(true));
    expect(result.current.publishStateError).toBe(false);
  });

  test('a FAILED fetch sets publishStateError and never fakes a workspace', async () => {
    // A persistent fetch error must be distinguishable from a real
    // "no workspace" (both otherwise leave hasWorkspace=false).
    fetchTaskPublishState.mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useTaskPublish('T1'));
    await waitFor(() => expect(result.current.publishStateError).toBe(true));
    expect(result.current.publishStateReady).toBe(false);  // never got a confirmed state
    expect(result.current.hasWorkspace).toBe(false);
  });

  test('exposes pull_request_urls (filtered to truthy)', async () => {
    fetchTaskPublishState.mockResolvedValue({ has_workspace: true });
    fetchTaskPullRequestState.mockResolvedValue({
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

  test('checks on tab load (mount), NOT on a timer', async () => {
    // The old 10s poll hammered the provider PR lookup and hung the
    // endpoint. Now we fetch once on tab load; advancing time must NOT
    // trigger another fetch.
    vi.useFakeTimers();
    try {
      fetchTaskPublishState
        .mockResolvedValueOnce({ has_workspace: false })
        .mockResolvedValueOnce({ has_workspace: true });

      const { result } = renderHook(() => useTaskPublish('T1'));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.hasWorkspace).toBe(false);
      expect(fetchTaskPublishState).toHaveBeenCalledTimes(1);

      // No polling — a full minute passes and there is still only one fetch.
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(fetchTaskPublishState).toHaveBeenCalledTimes(1);
      expect(result.current.hasWorkspace).toBe(false);  // never re-fetched
    } finally {
      vi.useRealTimers();
    }
  });

  test('a failed re-check keeps the last-known state', async () => {
    // A manual re-check (e.g. after a click) that FAILS must keep the last
    // known flags and just flag the error — not fake a "no workspace".
    fetchTaskPublishState
      .mockResolvedValueOnce({ has_workspace: true, has_changes_to_push: true })
      .mockRejectedValueOnce(new Error('network'));

    const { result } = renderHook(() => useTaskPublish('T1'));
    await waitFor(() => expect(result.current.hasWorkspace).toBe(true));

    await act(async () => { await result.current.refresh(); });

    expect(result.current.hasWorkspace).toBe(true);
    expect(result.current.hasChangesToPush).toBe(true);
    expect(result.current.publishStateError).toBe(true);
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
