// Tests for commentStore — the single source of truth for a task's diff
// / review comments. Verifies the subscribe/emit contract, that a
// mutation reconciles the cache for every subscriber, and — the bug this
// store was built to fix — that deleting the last comment removes it
// from the shared snapshot immediately (optimistically), so a stale
// file-tree badge can never linger behind the deleted thread.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';

const api = vi.hoisted(() => ({
  fetchTaskComments: vi.fn(),
  createTaskComment: vi.fn(),
  deleteTaskComment: vi.fn(),
  editTaskComment: vi.fn(),
  markTaskCommentAddressed: vi.fn(),
  reopenTaskComment: vi.fn(),
  resolveTaskComment: vi.fn(),
  retryTaskComment: vi.fn(),
}));
vi.mock('../api.js', () => api);

import { commentStore } from './commentStore.js';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// Collect every snapshot a subscriber sees, and return the latest.
function track(taskId) {
  const seen = [];
  const unsubscribe = commentStore.subscribe(taskId, (snap) => seen.push(snap));
  return { seen, unsubscribe, last: () => seen[seen.length - 1] };
}

beforeEach(() => {
  for (const fn of Object.values(api)) { fn.mockReset(); }
  api.fetchTaskComments.mockResolvedValue({ ok: true, body: { comments: [] } });
});
afterEach(() => { vi.useRealTimers(); });


describe('commentStore', () => {
  test('subscribe fetches once and delivers the comments to the subscriber', async () => {
    api.fetchTaskComments.mockResolvedValue({
      ok: true,
      body: { comments: [{ id: 'a', file_path: 'f.js', repo_id: 'r' }] },
    });
    const sub = track('T1');
    await flush();
    expect(api.fetchTaskComments).toHaveBeenCalledWith('T1');
    expect(sub.last().comments).toHaveLength(1);
    expect(sub.last().loading).toBe(false);
    sub.unsubscribe();
  });

  test('all subscribers to a task share one snapshot', async () => {
    api.fetchTaskComments.mockResolvedValue({
      ok: true, body: { comments: [{ id: 'a', file_path: 'f.js' }] },
    });
    const a = track('T1');
    const b = track('T1');
    await flush();
    // Second subscriber joined without a second fetch (single shared load).
    expect(api.fetchTaskComments).toHaveBeenCalledTimes(1);
    expect(a.last().comments).toBe(b.last().comments);
    a.unsubscribe();
    b.unsubscribe();
  });

  test('remove() drops the comment from the snapshot immediately (optimistic)', async () => {
    api.fetchTaskComments.mockResolvedValue({
      ok: true,
      body: { comments: [
        { id: 'root', file_path: 'f.js' },
        { id: 'reply', parent_id: 'root', file_path: 'f.js' },
        { id: 'other', file_path: 'g.js' },
      ] },
    });
    // deleteTaskComment never resolves here → proves the removal is
    // optimistic (the snapshot updates before the round-trip completes).
    api.deleteTaskComment.mockReturnValue(new Promise(() => {}));
    const sub = track('T1');
    await flush();
    expect(sub.last().comments).toHaveLength(3);

    commentStore.remove('T1', 'root');
    // Synchronous: the deleted root AND its reply are gone; the unrelated
    // comment on another file remains.
    const ids = sub.last().comments.map((c) => c.id);
    expect(ids).toEqual(['other']);
    sub.unsubscribe();
  });

  test('a mutation reconciles the cache from the server on success', async () => {
    api.fetchTaskComments
      .mockResolvedValueOnce({ ok: true, body: { comments: [{ id: 'a', file_path: 'f.js', kato_status: 'queued' }] } })
      .mockResolvedValue({ ok: true, body: { comments: [{ id: 'a', file_path: 'f.js', kato_status: 'addressed' }] } });
    api.resolveTaskComment.mockResolvedValue({ ok: true, body: {} });
    const sub = track('T1');
    await flush();
    expect(sub.last().comments[0].kato_status).toBe('queued');

    await commentStore.resolve('T1', 'a');
    await flush();
    expect(api.resolveTaskComment).toHaveBeenCalledWith('T1', 'a');
    expect(sub.last().comments[0].kato_status).toBe('addressed');
    sub.unsubscribe();
  });

  test('a failed fetch surfaces an error but keeps the last-known comments', async () => {
    api.fetchTaskComments
      .mockResolvedValueOnce({ ok: true, body: { comments: [{ id: 'a', file_path: 'f.js' }] } })
      .mockResolvedValueOnce({ ok: false, error: 'boom' });
    const sub = track('T1');
    await flush();
    expect(sub.last().comments).toHaveLength(1);

    commentStore.poke('T1');
    await flush();
    expect(sub.last().error).toContain('boom');
    expect(sub.last().comments).toHaveLength(1); // not blanked
    sub.unsubscribe();
  });

  test('the last unsubscribe drops the entry — a re-subscribe re-fetches', async () => {
    const a = track('T1');
    await flush();
    expect(api.fetchTaskComments).toHaveBeenCalledTimes(1);
    a.unsubscribe();

    const b = track('T1');
    await flush();
    expect(api.fetchTaskComments).toHaveBeenCalledTimes(2);
    b.unsubscribe();
  });

  test('poke on a task with no subscribers is a no-op', async () => {
    commentStore.poke('nobody');
    await flush();
    expect(api.fetchTaskComments).not.toHaveBeenCalled();
  });
});
