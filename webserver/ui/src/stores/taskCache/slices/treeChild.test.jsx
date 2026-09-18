// Wiring test for the file-tree slice: a first load paints the SERVER's cached
// tree, then replaces it with a fresh build — and every fetch after the first
// carries the ETag of the tree on screen, so an unchanged tree costs nothing.
//
// The operator: "can the repo loading and caching happen in the backend
// please?" The server keeps the copy (webserver/kato_webserver/file_tree_cache.py);
// what this proves is that the slice actually asks for it on a first load, never
// on a revalidate, follows a cached answer with exactly one fresh request, and
// re-parses nothing when the server answers "unchanged". Drives the real chain:
// treeChild → createDataStore → normalizeTrees, with only the network edge
// replaced.

import { describe, test, expect, beforeEach, vi } from 'vitest';

const { _api } = vi.hoisted(() => ({ _api: { responses: [], calls: [] } }));

vi.mock('../../../api.js', () => ({
  fetchFileTree: async (taskId, options = {}) => {
    _api.calls.push({
      taskId,
      cached: !!options.cached,
      signature: options.signature || '',
    });
    return _api.responses.length > 1 ? _api.responses.shift() : _api.responses[0];
  },
}));

const { treeChild } = await import('./treeChild.js');

const flush = () => new Promise((r) => setTimeout(r, 0));

const payloadWith = (repoId) => ({
  trees: [{ repo_id: repoId, cwd: `/w/${repoId}`, branch: 'UNA-1', tree: [{ name: 'a.js' }] }],
});
// What the api layer hands back for a 200: the tree, its ETag, and whether the
// server served it from its cache (a response header, not part of the tree).
const built = (repoId, etag = `"${repoId}"`) => ({
  payload: payloadWith(repoId), etag, cacheHit: false,
});
const fromCache = (repoId, etag = `"${repoId}"`) => ({
  payload: payloadWith(repoId), etag, cacheHit: true,
});
// A 304: the server confirming the tree the client already holds.
const unchanged = (etag) => ({ unchanged: true, etag, cacheHit: false });

beforeEach(() => {
  treeChild.clear();
  _api.responses = [];
  _api.calls = [];
});

describe('treeChild — the server caches the tree', () => {
  test('a first load paints the cached tree, then replaces it with a fresh build', async () => {
    let releaseFresh;
    const fresh = new Promise((resolve) => { releaseFresh = resolve; });
    _api.responses = [fromCache('cached-repo'), fresh];

    await treeChild.load('T1');
    await flush();
    // The fresh build is already asked for, and still out: the cached tree is
    // what is on screen meanwhile — real rows, not a spinner.
    expect(_api.calls.map((call) => call.cached)).toEqual([true, false]);
    expect(treeChild.get('T1').status).toBe('ready');
    expect(treeChild.get('T1').data[0].repo_id).toBe('cached-repo');

    releaseFresh(built('fresh-repo'));
    await flush(); await flush();
    expect(treeChild.get('T1').data[0].repo_id).toBe('fresh-repo');
    expect(_api.calls).toHaveLength(2);
  });

  test('with no cached copy the server builds fresh, and nothing is asked twice', async () => {
    _api.responses = [built('built-now')];

    await treeChild.load('T1');
    await flush(); await flush();
    expect(_api.calls).toEqual([{ taskId: 'T1', cached: true, signature: '' }]);
    expect(treeChild.get('T1').data[0].repo_id).toBe('built-now');
  });

  test('a revalidate over a tree on screen always builds fresh', async () => {
    _api.responses = [built('first')];
    await treeChild.load('T1');
    await flush();

    _api.responses = [fromCache('never-used')];
    await treeChild.load('T1');
    await flush(); await flush();
    expect(_api.calls.map((call) => call.cached)).toEqual([true, false]);
  });

  test('the cache flag never reaches the parsed tree', async () => {
    _api.responses = [fromCache('stored-repo'), new Promise(() => {})];
    await treeChild.load('T1');
    const [repo] = treeChild.get('T1').data;
    expect(repo).not.toHaveProperty('cacheHit');
    expect(repo).not.toHaveProperty('cache_hit');
    // normalizeTrees turns the changed/conflicted lists into Sets — a raw
    // payload would fail this and every tree row that calls .has().
    expect(repo.conflictedFiles).toBeInstanceOf(Set);
    expect(repo.changedFiles).toBeInstanceOf(Set);
  });
});

describe('treeChild — an unchanged tree costs nothing', () => {
  test('the tree on screen is offered back to the server as its ETag', async () => {
    _api.responses = [built('repo', '"etag-1"')];
    await treeChild.load('T1');
    await flush();

    await treeChild.load('T1');
    await flush();
    // First fetch had nothing to offer; the next one carries the tag of what
    // is on screen, which is what lets the server answer 304.
    expect(_api.calls.map((call) => call.signature)).toEqual(['', '"etag-1"']);
  });

  test('a 304 keeps the SAME parsed data — nothing re-parsed, nothing re-rendered', async () => {
    _api.responses = [built('repo', '"etag-1"')];
    await treeChild.load('T1');
    await flush();
    const painted = treeChild.get('T1').data;

    _api.responses = [unchanged('"etag-1"')];
    await treeChild.load('T1');
    await flush();
    // Referential identity is the whole point: the memoized tree rows bail.
    expect(treeChild.get('T1').data).toBe(painted);
    expect(treeChild.get('T1').status).toBe('ready');
  });

  test('a changed tree still replaces what is on screen', async () => {
    _api.responses = [built('repo', '"etag-1"')];
    await treeChild.load('T1');
    await flush();
    const painted = treeChild.get('T1').data;

    _api.responses = [built('repo-grew', '"etag-2"')];
    await treeChild.load('T1');
    await flush();
    expect(treeChild.get('T1').data).not.toBe(painted);
    expect(treeChild.get('T1').data[0].repo_id).toBe('repo-grew');
  });

  test('a purged task offers no ETag — it has no tree to be told about', async () => {
    // The signature comes from the STORE, so it cannot outlive the data it
    // describes: a stale tag would earn a 304 for a task holding nothing.
    _api.responses = [built('repo', '"etag-1"')];
    await treeChild.load('T1');
    await flush();
    treeChild.purge('T1');

    _api.responses = [built('repo', '"etag-1"')];
    await treeChild.load('T1');
    await flush();
    expect(_api.calls[_api.calls.length - 1].signature).toBe('');
    expect(treeChild.get('T1').data[0].repo_id).toBe('repo');
  });

  test('a server that sends no ETag still works, by comparing the tree itself', async () => {
    _api.responses = [built('repo', '')];
    await treeChild.load('T1');
    await flush();
    const painted = treeChild.get('T1').data;

    _api.responses = [built('repo', '')];
    await treeChild.load('T1');
    await flush();
    expect(treeChild.get('T1').data).toBe(painted);
  });
});
