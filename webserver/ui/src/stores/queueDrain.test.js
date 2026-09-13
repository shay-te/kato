// The queue-drain decision engine — plain data in, a decision out.
//
// The rule under test is the operator's: "unless i steer it, kato will run
// one prompt at a time." Every test here is a way that rule was, or could be,
// broken by a background task's poll-driven status.

import test from 'node:test';
import assert from 'node:assert/strict';

import { createQueueDrain, QUEUE_DRAIN_DECISION } from './queueDrain.js';

const { DELIVER, WAIT } = QUEUE_DRAIN_DECISION;

test('a task idle when first seen does NOT deliver — reload must not fire every queue', () => {
  const drain = createQueueDrain();
  assert.equal(drain.observe('A', false), WAIT);
  assert.equal(drain.observe('A', false), WAIT);
});

test('a task BUSY when first seen does not deliver either', () => {
  const drain = createQueueDrain();
  assert.equal(drain.observe('A', true), WAIT);
});

test('a turn ending (busy -> idle) delivers the next prompt', () => {
  const drain = createQueueDrain();
  drain.observe('A', true);
  assert.equal(drain.observe('A', false), DELIVER);
});

test('a stale idle right after a send cannot release the second prompt', () => {
  // The poll lags, so the reading just after a delivery can still say idle.
  // Idle -> idle is not an ending, so nothing more goes until the delivered
  // prompt is seen running and then stopping.
  const drain = createQueueDrain();
  drain.observe('A', true);
  assert.equal(drain.observe('A', false), DELIVER);   // prompt 1 sent
  assert.equal(drain.observe('A', false), WAIT);      // stale: not started
  assert.equal(drain.observe('A', false), WAIT);
  assert.equal(drain.observe('A', true), WAIT);       // prompt 1 running
  assert.equal(drain.observe('A', false), DELIVER);   // prompt 1 finished
});

test('prompts go strictly one per turn across a long run', () => {
  const drain = createQueueDrain();
  drain.observe('A', false); // seed
  const sequence = [true, false, false, true, true, false, false, false, true, false];
  const delivered = sequence.filter((busy) => drain.observe('A', busy) === DELIVER).length;
  // Three busy -> idle edges in that sequence: three turns, three prompts.
  assert.equal(delivered, 3);
});

test('tasks are independent — one ending never releases another', () => {
  const drain = createQueueDrain();
  drain.observe('A', true);
  drain.observe('B', true);
  assert.equal(drain.observe('A', false), DELIVER);
  assert.equal(drain.observe('B', true), WAIT);
});

test('forget re-seeds — leaving the focused tab is not that task\'s turn ending', () => {
  const drain = createQueueDrain();
  drain.observe('A', true);
  drain.forget('A');                              // became the focused task
  assert.equal(drain.observe('A', false), WAIT);  // re-seeded, not an edge
});

test('an empty task id is inert', () => {
  const drain = createQueueDrain();
  assert.equal(drain.observe('', true), WAIT);
  assert.equal(drain.observe('', false), WAIT);
  drain.forget('');
});
