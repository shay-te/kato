import { describe, test, expect, beforeEach, vi } from 'vitest';

// jsdom has no IndexedDB; a real Map with idbStore's contract stands in.
const { _mem, _state } = vi.hoisted(() => ({ _mem: new Map(), _state: { fail: false } }));

vi.mock('./idbStore.js', () => ({
  idbGet: async (key) => {
    if (_state.fail) { throw new Error('storage unavailable'); }
    return _mem.get(key);
  },
  idbDelete: async (key) => { _mem.delete(key); },
}));

const { removeLegacyTreeCache } = await import('./legacyTreeCacheCleanup.js');

beforeEach(() => {
  _mem.clear();
  _state.fail = false;
});

describe('removeLegacyTreeCache', () => {
  test('deletes every stored tree and the index, and nothing else', async () => {
    _mem.set('kato.taskCache.tree.index', ['UNA-1', 'UNA-2']);
    _mem.set('kato.taskCache.tree.UNA-1', '{"trees":[]}');
    _mem.set('kato.taskCache.tree.UNA-2', '{"trees":[]}');
    _mem.set('kato.composerImages.UNA-1', 'an image draft');

    await removeLegacyTreeCache();

    expect([..._mem.keys()]).toEqual(['kato.composerImages.UNA-1']);
  });

  test('nothing stored means nothing is touched', async () => {
    _mem.set('kato.composerImages.UNA-1', 'an image draft');
    await removeLegacyTreeCache();
    expect([..._mem.keys()]).toEqual(['kato.composerImages.UNA-1']);
  });

  test('unavailable storage is not an error', async () => {
    _state.fail = true;
    await expect(removeLegacyTreeCache()).resolves.toBeUndefined();
  });
});
