// Durable-payload adapter tests.
//
// jsdom has no IndexedDB, so ``idbStore.js`` — the shared best-effort engine —
// is mocked with an in-memory Map, the same pattern MessageForm.test.jsx uses
// for the composer's image drafts. Everything above it is the real module: the
// real key shaping, the real index, the real write queue.

import { describe, test, expect, beforeEach, vi } from 'vitest';

const { _mem, _fail } = vi.hoisted(() => ({ _mem: new Map(), _fail: { set: null } }));
vi.mock('../../utils/idbStore.js', () => ({
  // Snapshot at call time — a real readonly transaction reads the value as it
  // was when the transaction opened, not after a later write commits.
  idbGet: (key) => { const v = _mem.get(key); return Promise.resolve(v); },
  idbSet: async (key, value) => {
    if (_fail.set === key) { throw new Error('quota'); }
    _mem.set(key, value);
  },
  idbDelete: async (key) => { _mem.delete(key); },
}));

// Storage that is entirely unavailable: every op resolves undefined, which is
// exactly what idbStore does in private mode / node.
const UNAVAILABLE = { idbGet: async () => undefined, idbSet: async () => undefined, idbDelete: async () => undefined };

const { createPersistedPayloads } = await import('./persistedPayloads.js');

const INDEX = 'kato.taskCache.tree.index';
const payloadKey = (id) => `kato.taskCache.tree.${id}`;

beforeEach(() => { _mem.clear(); _fail.set = null; });

describe('createPersistedPayloads', () => {
  test('read is undefined when nothing was ever stored', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    expect(await p.read('T1')).toBeUndefined();
  });

  test('write then read round-trips the exact text', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    await p.write('T1', '{"trees":[]}');
    expect(await p.read('T1')).toBe('{"trees":[]}');
    expect(_mem.get(INDEX)).toEqual(['T1']);
  });

  test('keys are namespaced by name — two adapters never collide', async () => {
    const trees = createPersistedPayloads({ name: 'tree' });
    const diffs = createPersistedPayloads({ name: 'diff' });
    await trees.write('T1', 'tree-bytes');
    await diffs.write('T1', 'diff-bytes');
    expect(await trees.read('T1')).toBe('tree-bytes');
    expect(await diffs.read('T1')).toBe('diff-bytes');
  });

  test('writing past maxTasks evicts the oldest payload AND its index row', async () => {
    const p = createPersistedPayloads({ name: 'tree', maxTasks: 2 });
    await p.write('A', 'a');
    await p.write('B', 'b');
    await p.write('C', 'c');
    expect(_mem.get(INDEX)).toEqual(['C', 'B']);
    expect(await p.read('A')).toBeUndefined();          // evicted
    expect(_mem.has(payloadKey('A'))).toBe(false);      // and not orphaned
    expect(await p.read('B')).toBe('b');
    expect(await p.read('C')).toBe('c');
  });

  test('re-writing a task moves it to the front instead of duplicating it', async () => {
    const p = createPersistedPayloads({ name: 'tree', maxTasks: 2 });
    await p.write('A', 'a1');
    await p.write('B', 'b');
    await p.write('A', 'a2');
    expect(_mem.get(INDEX)).toEqual(['A', 'B']);
    expect(await p.read('A')).toBe('a2');
    expect(await p.read('B')).toBe('b');                // A's re-write did not evict B
  });

  test('overlapping writes never leak an unindexed payload', async () => {
    const p = createPersistedPayloads({ name: 'tree', maxTasks: 1 });
    // Not awaited individually — this is the burst the write queue exists for.
    await Promise.all([p.write('A', 'a'), p.write('B', 'b')]);
    expect(_mem.get(INDEX)).toEqual(['B']);
    // Index + exactly one payload. Unqueued, both writes read an empty index,
    // and A's payload would survive with nothing pointing at it — forever.
    expect(_mem.size).toBe(2);
    expect(_mem.has(payloadKey('A'))).toBe(false);
  });

  test('a payload over the size cap is not stored, and drops any stale copy', async () => {
    const p = createPersistedPayloads({ name: 'tree', maxChars: 10 });
    await p.write('T1', 'small');
    expect(await p.read('T1')).toBe('small');
    await p.write('T1', 'x'.repeat(11));
    expect(await p.read('T1')).toBeUndefined();
    expect(_mem.get(INDEX)).toEqual([]);
  });

  test('forget removes the payload and its index row', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    await p.write('A', 'a');
    await p.write('B', 'b');
    await p.forget('A');
    expect(await p.read('A')).toBeUndefined();
    expect(_mem.get(INDEX)).toEqual(['B']);
    expect(await p.read('B')).toBe('b');
  });

  test('a non-string stored value reads as undefined', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    _mem.set(payloadKey('T1'), { not: 'text' });
    expect(await p.read('T1')).toBeUndefined();
  });

  test('a corrupt index does not break the next write', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    _mem.set(INDEX, 'not-an-array');
    await p.write('T1', 'a');
    expect(_mem.get(INDEX)).toEqual(['T1']);
  });

  test('a failed write does not wedge the ones behind it', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    _fail.set = payloadKey('A');
    await p.write('A', 'a');
    _fail.set = null;
    await p.write('B', 'b');
    expect(await p.read('B')).toBe('b');
  });

  test('an empty task id is a no-op on every operation', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    await p.write('', 'a');
    await p.forget('');
    expect(await p.read('')).toBeUndefined();
    expect(_mem.size).toBe(0);
  });

  test('a non-string payload is never written', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    await p.write('T1', undefined);
    expect(_mem.size).toBe(0);
  });
});

describe('createPersistedPayloads — storage unavailable', () => {
  test('every operation degrades to a no-op instead of throwing', async () => {
    vi.resetModules();
    vi.doMock('../../utils/idbStore.js', () => UNAVAILABLE);
    const mod = await import('./persistedPayloads.js');
    const p = mod.createPersistedPayloads({ name: 'tree' });
    await expect(p.write('T1', 'a')).resolves.toBeUndefined();
    expect(await p.read('T1')).toBeUndefined();
    await expect(p.forget('T1')).resolves.toBeUndefined();
    vi.doUnmock('../../utils/idbStore.js');
    vi.resetModules();
  });
});

// The cap has to clear the case this cache was BUILT for. A 25-repo workspace
// serializes to roughly 4 MB (one large monorepo repo measures ~150 KB), and a
// cap near that would exclude precisely the workspaces whose reload is slow
// enough to matter.
describe('createPersistedPayloads — the default size cap', () => {
  test('a 25-repo-sized payload is persisted, not rejected', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    // Comfortably past the old 4 MB cap, which this exact workspace shape
    // would have brushed against.
    const bigTree = 'x'.repeat(6_000_000);
    await p.write('T1', bigTree);
    expect(await p.read('T1')).toBe(bigTree);
  });

  test('an absurd payload is still rejected', async () => {
    const p = createPersistedPayloads({ name: 'tree' });
    await p.write('T1', 'x'.repeat(16_000_001));
    expect(await p.read('T1')).toBeUndefined();
  });
});
