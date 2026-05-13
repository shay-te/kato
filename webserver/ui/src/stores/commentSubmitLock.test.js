import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import { commentSubmitLock } from './commentSubmitLock.js';

// Module-scoped state; must release between tests.
beforeEach(() => { commentSubmitLock.release(); });


test('isBusy() starts false', () => {
  assert.equal(commentSubmitLock.isBusy(), false);
});

test('acquire() returns true and flips isBusy', () => {
  const got = commentSubmitLock.acquire();
  assert.equal(got, true);
  assert.equal(commentSubmitLock.isBusy(), true);
});

test('second acquire() while held returns false (single-flight guard)', () => {
  commentSubmitLock.acquire();
  const second = commentSubmitLock.acquire();
  assert.equal(second, false);
  assert.equal(commentSubmitLock.isBusy(), true);
});

test('release() clears the busy flag', () => {
  commentSubmitLock.acquire();
  commentSubmitLock.release();
  assert.equal(commentSubmitLock.isBusy(), false);
});

test('release when not held is a no-op (does NOT emit)', () => {
  const seen = [];
  commentSubmitLock.subscribe((b) => seen.push(b));
  commentSubmitLock.release();
  // Only the initial-subscribe fire (false).
  assert.deepEqual(seen, [false]);
});

test('subscribers fire on acquire AND release', () => {
  const seen = [];
  const unsub = commentSubmitLock.subscribe((b) => seen.push(b));
  commentSubmitLock.acquire();
  commentSubmitLock.release();
  assert.deepEqual(seen, [false, true, false]);
  unsub();
});

test('unsubscribe stops further fires', () => {
  const seen = [];
  const unsub = commentSubmitLock.subscribe((b) => seen.push(b));
  unsub();
  commentSubmitLock.acquire();
  commentSubmitLock.release();
  // Only the initial-subscribe.
  assert.deepEqual(seen, [false]);
});

test('a thrown error in one subscriber does not break others', () => {
  const seen = [];
  commentSubmitLock.subscribe(() => { throw new Error('boom'); });
  commentSubmitLock.subscribe((b) => seen.push(b));
  commentSubmitLock.acquire();
  // Second subscriber still got both: initial-subscribe (false) + acquire (true).
  assert.deepEqual(seen, [false, true]);
});

test('acquire/release round-trip is idempotent (can be cycled repeatedly)', () => {
  for (let i = 0; i < 5; i += 1) {
    assert.equal(commentSubmitLock.acquire(), true);
    commentSubmitLock.release();
  }
  assert.equal(commentSubmitLock.isBusy(), false);
});
