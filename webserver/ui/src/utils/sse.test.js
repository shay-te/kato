import assert from 'node:assert/strict';
import test from 'node:test';

import { safeParseJSON } from './sse.js';


test('safeParseJSON returns the parsed value for valid JSON', () => {
  assert.deepEqual(safeParseJSON('{"a":1}'), { a: 1 });
  assert.deepEqual(safeParseJSON('[1,2,3]'), [1, 2, 3]);
  assert.deepEqual(safeParseJSON('null'), null);
  assert.deepEqual(safeParseJSON('"hello"'), 'hello');
  assert.deepEqual(safeParseJSON('42'), 42);
});

test('safeParseJSON returns null on invalid JSON (must NOT throw)', () => {
  // The whole point of the helper: SSE payloads can be malformed
  // (network blip, server bug). Callers must not crash.
  assert.equal(safeParseJSON('{not json'), null);
  assert.equal(safeParseJSON('{"a":'), null);
  assert.equal(safeParseJSON(''), null);
  assert.equal(safeParseJSON('undefined'), null);
});

test('safeParseJSON returns null for non-string input', () => {
  // null/undefined inputs trigger TypeError in JSON.parse — the
  // try/catch handles it.
  assert.equal(safeParseJSON(null), null);
  assert.equal(safeParseJSON(undefined), null);
});
