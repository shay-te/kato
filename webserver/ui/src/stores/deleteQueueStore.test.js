// The bulk-delete progress store.
//
// Operator report: "I don't know you are working, so I delete the tasks
// multiple times." Everything here exists to make a run legible while it is
// happening — which means the state has to live OUTSIDE the dialog, so
// closing it mid-run does not lose the progress.
//
// ``node:test``, not vitest: this is a pure helper with no DOM and no React,
// and vitest only includes ``*.test.jsx`` (see vitest.config.js).

import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import { DELETE_STATE, deleteQueue } from './deleteQueueStore.js';

// Module-scoped state means tests must clear between cases.
beforeEach(() => { deleteQueue.reset(); });

test('enqueue marks every id queued in ONE emit', () => {
  const seen = [];
  const unsubscribe = deleteQueue.subscribe((s) => seen.push(Object.keys(s).length));
  deleteQueue.enqueue(['A-1', 'A-2', 'A-3']);
  unsubscribe();
  // The initial fire-on-subscribe, then exactly one more for all three — not
  // three separate renders of a half-built list.
  assert.deepEqual(seen, [0, 3]);
  assert.equal(deleteQueue.snapshot()['A-2'].state, DELETE_STATE.QUEUED);
});

test('a run walks queued -> deleting -> done', () => {
  deleteQueue.enqueue(['A-1']);
  assert.equal(deleteQueue.snapshot()['A-1'].state, DELETE_STATE.QUEUED);
  deleteQueue.starting('A-1');
  assert.equal(deleteQueue.snapshot()['A-1'].state, DELETE_STATE.DELETING);
  deleteQueue.succeeded('A-1');
  assert.equal(deleteQueue.snapshot()['A-1'].state, DELETE_STATE.DONE);
});

test('a failure keeps its reason', () => {
  deleteQueue.enqueue(['A-1']);
  deleteQueue.failed('A-1', 'workspace directory still exists');
  const entry = deleteQueue.snapshot()['A-1'];
  assert.equal(entry.state, DELETE_STATE.FAILED);
  assert.match(entry.error, /still exists/);
});

test('isRunning covers BOTH queued and in-flight', () => {
  // A run with everything still waiting IS running — otherwise the dialog
  // would offer the delete button again before the first task started, which
  // is the double-delete this whole store exists to prevent.
  deleteQueue.enqueue(['A-1', 'A-2']);
  assert.equal(deleteQueue.isRunning(), true);
  deleteQueue.starting('A-1');
  assert.equal(deleteQueue.isRunning(), true);
  deleteQueue.succeeded('A-1');
  deleteQueue.succeeded('A-2');
  assert.equal(deleteQueue.isRunning(), false);
});

test('clearFinished keeps FAILED rows, drops DONE ones', () => {
  // A failure the operator has not read yet must survive closing the dialog;
  // a success is already visible as the task being gone from the strip.
  deleteQueue.enqueue(['OK-1', 'BAD-1']);
  deleteQueue.succeeded('OK-1');
  deleteQueue.failed('BAD-1', 'locked');
  deleteQueue.clearFinished();
  const snapshot = deleteQueue.snapshot();
  assert.equal(snapshot['OK-1'], undefined);
  assert.equal(snapshot['BAD-1'].state, DELETE_STATE.FAILED);
});

test('counts summarise the run', () => {
  deleteQueue.enqueue(['A-1', 'A-2', 'A-3']);
  deleteQueue.starting('A-1');
  deleteQueue.succeeded('A-2');
  deleteQueue.failed('A-3', 'nope');
  assert.deepEqual(deleteQueue.counts(), {
    queued: 0, deleting: 1, done: 1, failed: 1,
  });
});

test('blank ids are ignored rather than creating empty rows', () => {
  deleteQueue.enqueue(['', '   ', null, undefined, 'A-1']);
  assert.deepEqual(Object.keys(deleteQueue.snapshot()), ['A-1']);
});

test('subscribers are notified on every transition', () => {
  let calls = 0;
  const unsubscribe = deleteQueue.subscribe(() => { calls += 1; });
  const initial = calls;
  deleteQueue.enqueue(['A-1']);
  deleteQueue.starting('A-1');
  deleteQueue.succeeded('A-1');
  unsubscribe();
  assert.equal(calls - initial, 3);
});

test('a throwing subscriber cannot break the run', () => {
  // The dialog is one subscriber among several; a render error in it must not
  // stop the queue from advancing.
  const unsubscribe = deleteQueue.subscribe(() => { throw new Error('boom'); });
  deleteQueue.enqueue(['A-1']);
  deleteQueue.succeeded('A-1');
  unsubscribe();
  assert.equal(deleteQueue.snapshot()['A-1'].state, DELETE_STATE.DONE);
});
