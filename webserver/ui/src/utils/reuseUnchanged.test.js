// A polled list keeps the identity of whatever did not change.

import test from 'node:test';
import assert from 'node:assert/strict';

import { reuseUnchanged } from './reuseUnchanged.js';

const keyOf = (item) => item.id;

test('nothing changed: the same list comes back, so no state update happens', () => {
  const previous = [{ id: 'a', n: 1 }, { id: 'b', n: 2 }];
  const next = [{ id: 'a', n: 1 }, { id: 'b', n: 2 }];
  assert.equal(reuseUnchanged(previous, next, keyOf), previous);
});

test('one item changed: only that one is new', () => {
  const previous = [{ id: 'a', n: 1 }, { id: 'b', n: 2 }];
  const result = reuseUnchanged(previous, [{ id: 'a', n: 1 }, { id: 'b', n: 3 }], keyOf);
  assert.notEqual(result, previous);
  assert.equal(result[0], previous[0]);
  assert.deepEqual(result[1], { id: 'b', n: 3 });
});

test('an added item makes a new list and keeps the others', () => {
  const previous = [{ id: 'a' }];
  const result = reuseUnchanged(previous, [{ id: 'a' }, { id: 'b' }], keyOf);
  assert.equal(result.length, 2);
  assert.equal(result[0], previous[0]);
});

test('a removed item makes a new list', () => {
  const previous = [{ id: 'a' }, { id: 'b' }];
  const result = reuseUnchanged(previous, [{ id: 'a' }], keyOf);
  assert.equal(result.length, 1);
  assert.equal(result[0], previous[0]);
});

test('a new order is a change, and the items keep their identity', () => {
  const previous = [{ id: 'a' }, { id: 'b' }];
  const result = reuseUnchanged(previous, [{ id: 'b' }, { id: 'a' }], keyOf);
  assert.notEqual(result, previous);
  assert.equal(result[0], previous[1]);
  assert.equal(result[1], previous[0]);
});

test('an empty list stays the same list', () => {
  const previous = [];
  assert.equal(reuseUnchanged(previous, [], keyOf), previous);
});
