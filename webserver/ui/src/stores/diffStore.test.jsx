// Tests for diffStore — the single source of truth for a task's parsed
// changeset. Verifies one shared fetch/parse across subscribers, the
// signature guard's referential stability, poke, error-keeps-last, and
// entry teardown on last unsubscribe.

import { describe, test, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({ fetchDiff: vi.fn() }));
vi.mock('../api.js', () => api);
// parseRepoDiffs is pure; stub it to a trivial shape so the test asserts
// store behaviour (fetch/cache/guard), not the diff parser.
vi.mock('../diffModel.js', () => ({
  parseRepoDiffs: (payload) => (payload?.repos || []),
}));

import { diffStore } from './diffStore.js';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function track(taskId) {
  const seen = [];
  const unsubscribe = diffStore.subscribe(taskId, (snap) => seen.push(snap));
  return { seen, unsubscribe, last: () => seen[seen.length - 1] };
}

beforeEach(() => {
  api.fetchDiff.mockReset();
  api.fetchDiff.mockResolvedValue({ repos: [] });
});


describe('diffStore', () => {
  test('subscribe fetches + parses once and delivers repoDiffs', async () => {
    api.fetchDiff.mockResolvedValue({ repos: [{ repo_id: 'r', files: [] }] });
    const sub = track('T1');
    await flush();
    expect(api.fetchDiff).toHaveBeenCalledWith('T1');
    expect(sub.last().repoDiffs).toEqual([{ repo_id: 'r', files: [] }]);
    expect(sub.last().loading).toBe(false);
    sub.unsubscribe();
  });

  test('two subscribers share one fetch and the same repoDiffs identity', async () => {
    api.fetchDiff.mockResolvedValue({ repos: [{ repo_id: 'r', files: [] }] });
    const a = track('T1');
    const b = track('T1');
    await flush();
    expect(api.fetchDiff).toHaveBeenCalledTimes(1);
    expect(a.last().repoDiffs).toBe(b.last().repoDiffs);
    a.unsubscribe();
    b.unsubscribe();
  });

  test('an unchanged payload keeps the same repoDiffs reference (guard)', async () => {
    api.fetchDiff.mockResolvedValue({ repos: [{ repo_id: 'r' }] });
    const sub = track('T1');
    await flush();
    const first = sub.last().repoDiffs;
    await diffStore.poke('T1'); // same bytes back
    await flush();
    expect(sub.last().repoDiffs).toBe(first); // no new identity → memos bail
    sub.unsubscribe();
  });

  test('poke picks up a changed payload', async () => {
    api.fetchDiff
      .mockResolvedValueOnce({ repos: [{ repo_id: 'a' }] })
      .mockResolvedValue({ repos: [{ repo_id: 'a' }, { repo_id: 'b' }] });
    const sub = track('T1');
    await flush();
    expect(sub.last().repoDiffs).toHaveLength(1);
    await diffStore.poke('T1');
    await flush();
    expect(sub.last().repoDiffs).toHaveLength(2);
    sub.unsubscribe();
  });

  test('a failed fetch surfaces an error but keeps the last changeset', async () => {
    api.fetchDiff
      .mockResolvedValueOnce({ repos: [{ repo_id: 'r' }] })
      .mockRejectedValueOnce(new Error('boom'));
    const sub = track('T1');
    await flush();
    expect(sub.last().repoDiffs).toHaveLength(1);
    await diffStore.poke('T1');
    await flush();
    expect(sub.last().error).toContain('boom');
    expect(sub.last().repoDiffs).toHaveLength(1); // not blanked
    sub.unsubscribe();
  });

  test('the last unsubscribe drops the entry — a re-subscribe re-fetches', async () => {
    const a = track('T1');
    await flush();
    expect(api.fetchDiff).toHaveBeenCalledTimes(1);
    a.unsubscribe();
    const b = track('T1');
    await flush();
    expect(api.fetchDiff).toHaveBeenCalledTimes(2);
    b.unsubscribe();
  });

  test('poke on a task with no subscribers is a no-op', async () => {
    diffStore.poke('nobody');
    await flush();
    expect(api.fetchDiff).not.toHaveBeenCalled();
  });
});
