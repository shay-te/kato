// Tests for the dom utility helpers — both pure functions, both
// previously untested. ``cssEscapeAttr`` is used to build dynamic
// CSS attribute selectors; a bug here lets values with quotes /
// backslashes break the selector and silently mismatch.

import assert from 'node:assert/strict';
import test from 'node:test';

import { cssEscapeAttr, stringifyShort } from './dom.js';


// ---------------------------------------------------------------------------
// cssEscapeAttr
// ---------------------------------------------------------------------------

test('cssEscapeAttr: uses CSS.escape when available', function () {
  // Browser environment: CSS.escape is the canonical implementation.
  const originalCSS = globalThis.CSS;
  globalThis.CSS = { escape: (s) => `ESCAPED(${s})` };
  try {
    assert.equal(cssEscapeAttr('foo'), 'ESCAPED(foo)');
  } finally {
    if (originalCSS === undefined) {
      delete globalThis.CSS;
    } else {
      globalThis.CSS = originalCSS;
    }
  }
});

test('cssEscapeAttr: fallback escapes quotes and backslashes', function () {
  // Node test environment lacks `CSS`; the fallback path runs and
  // must escape characters that would break an attribute selector.
  const originalCSS = globalThis.CSS;
  delete globalThis.CSS;
  try {
    assert.equal(cssEscapeAttr('hello "world"'), 'hello \\"world\\"');
    assert.equal(cssEscapeAttr('back\\slash'), 'back\\\\slash');
    assert.equal(cssEscapeAttr('safe-value-123'), 'safe-value-123');
  } finally {
    if (originalCSS !== undefined) {
      globalThis.CSS = originalCSS;
    }
  }
});

test('cssEscapeAttr: coerces non-string input to string first', function () {
  // Numbers, booleans, etc. must not throw — coerced before escape.
  const originalCSS = globalThis.CSS;
  delete globalThis.CSS;
  try {
    assert.equal(cssEscapeAttr(42), '42');
    assert.equal(cssEscapeAttr(true), 'true');
    assert.equal(cssEscapeAttr(null), 'null');
  } finally {
    if (originalCSS !== undefined) {
      globalThis.CSS = originalCSS;
    }
  }
});


// ---------------------------------------------------------------------------
// stringifyShort
// ---------------------------------------------------------------------------

test('stringifyShort: returns full JSON when under cap', function () {
  assert.equal(stringifyShort({ a: 1 }, 120), '{"a":1}');
  assert.equal(stringifyShort([1, 2, 3], 120), '[1,2,3]');
});

test('stringifyShort: truncates with ellipsis when over cap', function () {
  // Cap is the TOTAL output length including the ellipsis.
  const result = stringifyShort({ a: 'x'.repeat(200) }, 50);
  assert.equal(result.length, 50);
  assert.ok(result.endsWith('…'));
});

test('stringifyShort: respects exact cap boundary', function () {
  // A 50-char JSON should not truncate when cap is 50.
  const value = { v: 'x'.repeat(40) };
  const full = JSON.stringify(value);
  if (full.length <= 50) {
    assert.equal(stringifyShort(value, 50), full);
  }
});

test('stringifyShort: returns "" for unstringifiable input (circular)', function () {
  // Circular refs would otherwise throw — must return empty
  // string so callers can use the result unguarded.
  const circular = {};
  circular.self = circular;
  assert.equal(stringifyShort(circular), '');
});

test('stringifyShort: returns "" for undefined', function () {
  // JSON.stringify(undefined) returns undefined (not a string).
  // The function falls through to '' so callers don't see "undefined".
  assert.equal(stringifyShort(undefined), '');
});

test('stringifyShort: uses default cap of 120', function () {
  // No-arg cap. A 200-char JSON should be truncated to 120.
  const big = { v: 'x'.repeat(500) };
  const result = stringifyShort(big);
  assert.equal(result.length, 120);
});

test('stringifyShort: handles null cleanly', function () {
  // JSON.stringify(null) is "null" — a valid string under any cap.
  assert.equal(stringifyShort(null), 'null');
});

test('stringifyShort: empty object/array stringify without truncation', function () {
  assert.equal(stringifyShort({}), '{}');
  assert.equal(stringifyShort([]), '[]');
});
