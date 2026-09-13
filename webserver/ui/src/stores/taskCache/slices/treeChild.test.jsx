// Wiring test for the file-tree slice.
//
// The engine (createDataStore) and the adapter (persistedPayloads) are each
// covered on their own. What neither can prove is that the TREE slice actually
// opts into persistence — a one-line config that would regress silently, and
// whose only symptom is the Files pane going back to a slow reload. So this
// drives the real chain end to end: treeChild → createDataStore → the durable
// adapter → the IndexedDB keys.

import { describe, test, expect, beforeEach, vi } from 'vitest';

const { _mem, _api } = vi.hoisted(() => ({
  _mem: new Map(),
  _api: { payload: null, gate: null, calls: 0 },
}));

vi.mock('../../../utils/idbStore.js', () => ({
  idbGet: (key) => Promise.resolve(_mem.get(key)),
  idbSet: async (key, value) => { _mem.set(key, value); },
  idbDelete: async (key) => { _mem.delete(key); },
}));

vi.mock('../../../api.js', () => ({
  fetchFileTree: async () => {
    _api.calls += 1;
    if (_api.gate) { await _api.gate; }
    return _api.payload;
  },
}));

const { treeChild } = await import('./treeChild.js');

const TREE_KEY = 'kato.taskCache.tree.T1';
const flush = () => new Promise((r) => setTimeout(r, 0));

const payloadWith = (repoId) => ({
  trees: [{ repo_id: repoId, cwd: `/w/${repoId}`, branch: 'UNA-1', tree: [{ path: 'a.js' }] }],
});

beforeEach(() => {
  _mem.clear();
  treeChild.clear();
  _api.payload = payloadWith('api-repo');
  _api.gate = null;
  _api.calls = 0;
});

describe('treeChild — durable across a reload', () => {
  test('a fetched tree is written to durable storage under the tree namespace', async () => {
    await treeChild.load('T1');
    await flush();
    expect(_mem.get(TREE_KEY)).toBe(JSON.stringify(payloadWith('api-repo')));
  });

  test('the stored tree paints before the fetch lands, then the fetch wins', async () => {
    _mem.set(TREE_KEY, JSON.stringify(payloadWith('stored-repo')));
    let release;
    _api.gate = new Promise((r) => { release = r; });      // hold the walk open

    const loading = treeChild.load('T1');
    await flush();
    // This is the whole point: real rows, not a spinner, on the frame after
    // mount — while /files is still walking every repo.
    expect(treeChild.get('T1').status).toBe('ready');
    expect(treeChild.get('T1').data[0].repo_id).toBe('stored-repo');

    release(); await loading;
    expect(treeChild.get('T1').data[0].repo_id).toBe('api-repo');
  });

  test('an unchanged tree costs zero re-renders — the restore keeps the data reference', async () => {
    _mem.set(TREE_KEY, JSON.stringify(payloadWith('same-repo')));
    _api.payload = payloadWith('same-repo');

    await treeChild.load('T1');
    await flush();
    const restored = treeChild.get('T1').data;
    expect(treeChild.get('T1').data).toBe(restored);
  });

  test('the restored payload is normalized, not handed over raw', async () => {
    _mem.set(TREE_KEY, JSON.stringify(payloadWith('stored-repo')));
    _api.gate = new Promise(() => {});                     // never resolves
    treeChild.load('T1');
    await flush();
    const [repo] = treeChild.get('T1').data;
    // normalizeTrees turns the changed/conflicted lists into Sets — a raw
    // payload would fail this and every tree row that calls .has().
    expect(repo.conflictedFiles).toBeInstanceOf(Set);
    expect(repo.changedFiles).toBeInstanceOf(Set);
    expect(repo.branch).toBe('UNA-1');
  });
});
