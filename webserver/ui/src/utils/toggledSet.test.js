import assert from 'node:assert/strict';
import test from 'node:test';

import { toggledSet } from './toggledSet.js';

test('adds a value that was missing', function () {
  assert.deepEqual([...toggledSet(new Set(['a']), 'b')], ['a', 'b']);
});

test('removes a value that was there', function () {
  assert.deepEqual([...toggledSet(new Set(['a', 'b']), 'a')], ['b']);
});

test('never mutates the set it was given', function () {
  const original = new Set(['a']);
  const next = toggledSet(original, 'b');
  assert.notEqual(next, original);
  assert.deepEqual([...original], ['a']);
});
