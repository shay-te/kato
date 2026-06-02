import assert from 'node:assert/strict';
import test from 'node:test';

import {
  imageDraftKey,
  readImageDraft,
  writeImageDraft,
  clearImageDraft,
  IMAGE_DRAFT_PREFIX,
} from './composerImageDraft.js';

// The durable round-trip (write image → reload → read it back) needs a real
// IndexedDB and is covered in MessageForm.test.jsx against a mocked store.
// Here we pin the pure, storage-independent contract: per-task keys and
// best-effort behavior when IndexedDB is unavailable (node).

test('imageDraftKey is per-task, blank for no task', () => {
  assert.equal(imageDraftKey('T1'), `${IMAGE_DRAFT_PREFIX}T1`);
  assert.equal(imageDraftKey(''), '');
  assert.equal(imageDraftKey(null), '');
});

test('read returns [] when storage is unavailable (node) or task blank', async () => {
  assert.deepEqual(await readImageDraft('T1'), []);
  assert.deepEqual(await readImageDraft(''), []);
});

test('write / clear are best-effort and never throw', async () => {
  await writeImageDraft('T1', [{ media_type: 'image/png', data: 'abc' }]);
  await writeImageDraft('T1', []);
  await clearImageDraft('T1');
  await writeImageDraft('', [{ media_type: 'image/png', data: 'abc' }]);
});
