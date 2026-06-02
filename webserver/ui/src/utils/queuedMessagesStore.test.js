import assert from 'node:assert/strict';
import test from 'node:test';

import {
  readQueuedMessages,
  writeQueuedMessages,
  persistQueuedMessages,
  hydrateQueuedMessages,
  forgetQueuedMessages,
  _resetQueuedMessagesStore,
} from './queuedMessagesStore.js';

function _items(n) {
  return Array.from({ length: n }, (_, i) => ({ id: `q-${i}`, text: `m${i}`, images: [] }));
}

test('read returns [] for an unknown / blank task', () => {
  assert.deepEqual(readQueuedMessages('never-seen'), []);
  assert.deepEqual(readQueuedMessages(''), []);
  assert.deepEqual(readQueuedMessages(null), []);
});

test('write then read round-trips per task (survives a "remount")', () => {
  const a = _items(2);
  writeQueuedMessages('TASK-A', a);
  // A different "mount" reading the same task gets the queue back.
  assert.deepEqual(readQueuedMessages('TASK-A'), a);
});

test('queues are isolated per task (task A never leaks into task B)', () => {
  writeQueuedMessages('TASK-A', _items(2));
  writeQueuedMessages('TASK-B', _items(1));
  assert.equal(readQueuedMessages('TASK-A').length, 2);
  assert.equal(readQueuedMessages('TASK-B').length, 1);
});

test('writing an empty queue drops the entry (no unbounded growth)', () => {
  writeQueuedMessages('TASK-C', _items(1));
  assert.equal(readQueuedMessages('TASK-C').length, 1);
  writeQueuedMessages('TASK-C', []);
  assert.deepEqual(readQueuedMessages('TASK-C'), []);
});

test('blank taskId writes are ignored', () => {
  writeQueuedMessages('', _items(1));
  assert.deepEqual(readQueuedMessages(''), []);
});

test('hydrate returns the warm in-memory queue without touching durable storage', async () => {
  _resetQueuedMessagesStore();
  const a = _items(2);
  writeQueuedMessages('TASK-WARM', a);
  // A warm Map (an ordinary tab switch) wins — returned as-is.
  assert.deepEqual(await hydrateQueuedMessages('TASK-WARM'), a);
});

test('hydrate returns [] for a cold task when durable storage is unavailable', async () => {
  _resetQueuedMessagesStore();
  // node has no IndexedDB → idb best-effort no-ops → empty restore, no throw.
  assert.deepEqual(await hydrateQueuedMessages('TASK-COLD'), []);
  assert.deepEqual(await hydrateQueuedMessages(''), []);
});

test('persist is best-effort and never throws (durable storage unavailable)', async () => {
  // Returns a promise that resolves; no IndexedDB in node, so it's a no-op.
  await persistQueuedMessages('TASK-P', _items(1));
  await persistQueuedMessages('TASK-P', []);
  await persistQueuedMessages('', _items(1));
});

test('forget wipes a task from the in-memory Map (durable purge is best-effort)', async () => {
  _resetQueuedMessagesStore();
  writeQueuedMessages('TASK-FORGET', _items(2));
  assert.equal(readQueuedMessages('TASK-FORGET').length, 2);
  await forgetQueuedMessages('TASK-FORGET');
  assert.deepEqual(readQueuedMessages('TASK-FORGET'), []);
  await forgetQueuedMessages(''); // blank no-op, never throws
});
