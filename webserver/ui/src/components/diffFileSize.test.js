import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  LARGE_FILE_LINE_THRESHOLD,
  countDiffLines,
  isLargeFile,
} from './diffFileSize.js';

// Threshold + counting rule are load-bearing for the auto-collapse
// behaviour in DiffFileWithComments. Pinned here so a future tweak
// of the threshold can't silently start letting 10K-line generated
// diffs render unexpanded again.

test('countDiffLines sums change-line counts across all hunks', () => {
  const file = {
    hunks: [
      { changes: [{}, {}, {}] },
      { changes: [{}, {}] },
    ],
  };
  assert.equal(countDiffLines(file), 5);
});

test('countDiffLines tolerates missing or malformed input', () => {
  assert.equal(countDiffLines(null), 0);
  assert.equal(countDiffLines({}), 0);
  assert.equal(countDiffLines({ hunks: null }), 0);
  assert.equal(countDiffLines({ hunks: [{ changes: null }, null] }), 0);
});

test('isLargeFile returns false for empty / small files (operator flow unchanged)', () => {
  assert.equal(isLargeFile({ hunks: [] }), false);
  const small = { hunks: [{ changes: new Array(50).fill({}) }] };
  assert.equal(isLargeFile(small), false);
});

test('isLargeFile flips true above the threshold so render auto-collapses', () => {
  const big = {
    hunks: [{ changes: new Array(LARGE_FILE_LINE_THRESHOLD + 1).fill({}) }],
  };
  assert.equal(isLargeFile(big), true);
});

test('isLargeFile is exclusive at the threshold (exactly THRESHOLD lines still inlines)', () => {
  const exact = {
    hunks: [{ changes: new Array(LARGE_FILE_LINE_THRESHOLD).fill({}) }],
  };
  assert.equal(isLargeFile(exact), false);
});
