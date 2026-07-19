// catalogStore tests — NO mocks. Real module cache + a real in-memory fetcher
// (a plain async fn whose value/failure we control). Proves per-key caching,
// force-refresh, failed-fetch-not-cached, and clear.

import assert from 'node:assert/strict';
import test from 'node:test';

import { loadCatalog, clearCatalogCache } from './catalogStore.js';

function makeFetcher(value) {
  const s = { value, calls: 0, lastForce: undefined, fail: null };
  const fetch = async (force) => {
    s.calls += 1;
    s.lastForce = force;
    if (s.fail) { throw new Error(s.fail); }
    return s.value;
  };
  return { fetch, s };
}

test('caches per key — a second load does NOT re-fetch', async () => {
  clearCatalogCache();
  const { fetch, s } = makeFetcher({ models: [1] });
  const a = await loadCatalog('models', fetch);
  const b = await loadCatalog('models', fetch);
  assert.deepEqual(a, { models: [1] });
  assert.equal(b, a);
  assert.equal(s.calls, 1);
});

test('distinct keys fetch independently', async () => {
  clearCatalogCache();
  const m = makeFetcher({ models: [1] });
  const e = makeFetcher({ levels: ['high'] });
  await loadCatalog('models', m.fetch);
  await loadCatalog('levels', e.fetch);
  assert.equal(m.s.calls, 1);
  assert.equal(e.s.calls, 1);
});

test('force re-fetches, replaces the cache, and passes force to the fetcher', async () => {
  clearCatalogCache();
  const { fetch, s } = makeFetcher({ models: [1] });
  await loadCatalog('models', fetch);
  s.value = { models: [1, 2] };
  const forced = await loadCatalog('models', fetch, true);
  assert.deepEqual(forced, { models: [1, 2] });
  assert.equal(s.calls, 2);
  assert.equal(s.lastForce, true);
  const after = await loadCatalog('models', fetch);
  assert.deepEqual(after, { models: [1, 2] });
  assert.equal(s.calls, 2); // read the replaced cache, no extra fetch
});

test('a failed fetch is NOT cached — the next call retries', async () => {
  clearCatalogCache();
  const { fetch, s } = makeFetcher({ models: [1] });
  s.fail = 'boom';
  await assert.rejects(() => loadCatalog('models', fetch));
  s.fail = null;
  const ok = await loadCatalog('models', fetch);
  assert.deepEqual(ok, { models: [1] });
  assert.equal(s.calls, 2);
});

test('clearCatalogCache drops entries — next load re-fetches', async () => {
  clearCatalogCache();
  const { fetch, s } = makeFetcher({ models: [1] });
  await loadCatalog('models', fetch);
  clearCatalogCache();
  await loadCatalog('models', fetch);
  assert.equal(s.calls, 2);
});
