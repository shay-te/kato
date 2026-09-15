// Wiring test for the file-tree slice: a first load paints the SERVER's cached
// tree, then replaces it with a fresh build.
//
// The operator: "can the repo loading and caching happen in the backend
// please?" The server keeps the copy (webserver/kato_webserver/file_tree_cache.py);
// what this proves is that the slice actually asks for it on a first load, never
// on a revalidate, and follows a cached answer with exactly one fresh request.
// Drives the real chain: treeChild → createDataStore → normalizeTrees, with only
// the network edge replaced.

import { describe, test, expect, beforeEach, vi } from 'vitest';

const { _api } = vi.hoisted(() => ({ _api: { responses: [], calls: [] } }));

vi.mock('../../../api.js', () => ({
  fetchFileTree: async (taskId, options = {}) => {
    _api.calls.push({ taskId, cached: !!options.cached });
    return _api.responses.length > 1 ? _api.responses.shift() : _api.responses[0];
  },
}));

const { treeChild } = await import('./treeChild.js');

const flush = () => new Promise((r) => setTimeout(r, 0));

const payloadWith = (repoId) => ({
  trees: [{ repo_id: repoId, cwd: `/w/${repoId}`, branch: 'UNA-1', tree: [{ path: 'a.js' }] }],
});
const cachedPayloadWith = (repoId) => ({ cache_hit: true, ...payloadWith(repoId) });

beforeEach(() => {
  treeChild.clear();
  _api.responses = [];
  _api.calls = [];
});

describe('treeChild — the server caches the tree', () => {
  test('a first load paints the cached tree, then replaces it with a fresh build', async () => {
    let releaseFresh;
    const fresh = new Promise((resolve) => { releaseFresh = resolve; });
    _api.responses = [cachedPayloadWith('cached-repo'), fresh];

    await treeChild.load('T1');
    await flush();
    // The fresh build is already asked for, and still out: the cached tree is
    // what is on screen meanwhile — real rows, not a spinner.
    expect(_api.calls).toEqual([
      { taskId: 'T1', cached: true },
      { taskId: 'T1', cached: false },
    ]);
    expect(treeChild.get('T1').status).toBe('ready');
    expect(treeChild.get('T1').data[0].repo_id).toBe('cached-repo');

    releaseFresh(payloadWith('fresh-repo'));
    await flush(); await flush();
    expect(treeChild.get('T1').data[0].repo_id).toBe('fresh-repo');
    expect(_api.calls).toHaveLength(2);
  });

  test('an unchanged fresh tree keeps the painted data — no re-render', async () => {
    _api.responses = [cachedPayloadWith('same-repo'), payloadWith('same-repo')];

    await treeChild.load('T1');
    const painted = treeChild.get('T1').data;
    await flush(); await flush();
    expect(_api.calls).toHaveLength(2);
    expect(treeChild.get('T1').data).toBe(painted);
  });

  test('with no cached copy the server builds fresh, and nothing is asked twice', async () => {
    _api.responses = [payloadWith('built-now')];

    await treeChild.load('T1');
    await flush(); await flush();
    expect(_api.calls).toEqual([{ taskId: 'T1', cached: true }]);
    expect(treeChild.get('T1').data[0].repo_id).toBe('built-now');
  });

  test('a revalidate over a tree on screen always builds fresh', async () => {
    _api.responses = [payloadWith('first')];
    await treeChild.load('T1');
    await flush();

    _api.responses = [cachedPayloadWith('never-used')];
    await treeChild.load('T1');
    await flush(); await flush();
    expect(_api.calls.map((call) => call.cached)).toEqual([true, false]);
  });

  test('the cache flag never reaches the parsed tree', async () => {
    _api.responses = [cachedPayloadWith('stored-repo'), new Promise(() => {})];
    await treeChild.load('T1');
    const [repo] = treeChild.get('T1').data;
    expect(repo).not.toHaveProperty('cache_hit');
    // normalizeTrees turns the changed/conflicted lists into Sets — a raw
    // payload would fail this and every tree row that calls .has().
    expect(repo.conflictedFiles).toBeInstanceOf(Set);
    expect(repo.changedFiles).toBeInstanceOf(Set);
  });
});
