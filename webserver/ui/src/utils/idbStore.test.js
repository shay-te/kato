import assert from 'node:assert/strict';
import test from 'node:test';

import { idbGet, idbSet, idbDelete } from './idbStore.js';

// node has no global ``indexedDB``, so every operation must degrade to a
// best-effort no-op (resolve, never throw) — the composer keeps working, just
// without cross-reload image persistence. (The happy-path round trip is
// exercised against a mocked IndexedDB in the component vitest suites.)

test('all ops resolve to a no-op when IndexedDB is unavailable', async () => {
  assert.equal(await idbGet('k'), undefined);
  assert.equal(await idbSet('k', { a: 1 }), undefined);
  assert.equal(await idbDelete('k'), undefined);
});

test('blank keys are a no-op', async () => {
  assert.equal(await idbGet(''), undefined);
  assert.equal(await idbSet('', 1), undefined);
  assert.equal(await idbDelete(''), undefined);
});
