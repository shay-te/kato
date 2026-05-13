import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  CUMULATIVE_EXPANDED_LINE_BUDGET,
  LARGE_FILE_LINE_THRESHOLD,
  countDiffLines,
  decideAutoExpand,
  isLargeFile,
} from './diffFileSize.js';


function _file(lineCount) {
  return { hunks: [{ changes: new Array(lineCount).fill({}) }] };
}

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


// ---------------------------------------------------------------------------
// decideAutoExpand: cumulative-budget rule.
// ---------------------------------------------------------------------------

test('decideAutoExpand returns empty array for empty input', () => {
  assert.deepEqual(decideAutoExpand([]), []);
  assert.deepEqual(decideAutoExpand(null), []);
  assert.deepEqual(decideAutoExpand(undefined), []);
});

test('decideAutoExpand expands every file when total stays under budget', () => {
  // 4 × 200-line files = 800 lines total, well under the 2000 budget.
  const files = [_file(200), _file(200), _file(200), _file(200)];
  assert.deepEqual(decideAutoExpand(files), [true, true, true, true]);
});

test('decideAutoExpand collapses files past the cumulative budget (Bug fix)', () => {
  // Operator-reported bug: many medium files made the page lag even
  // though no single file was large. With 20 × 200-line files (4 000
  // lines total), the cumulative budget (2 000) must cap the
  // auto-expanded count.
  const files = new Array(20).fill(0).map(() => _file(200));
  const expanded = decideAutoExpand(files);

  const expandedCount = expanded.filter(Boolean).length;
  const cumulativeExpandedLines = expanded.reduce(
    (sum, isExpanded, i) => sum + (isExpanded ? countDiffLines(files[i]) : 0),
    0,
  );

  // Some files must be collapsed.
  assert.ok(
    expandedCount < files.length,
    `expected SOME files to collapse for cumulative-line budget, but `
    + `${expandedCount}/${files.length} were expanded — page would `
    + `freeze on render`,
  );
  // Total expanded lines stays at or below the budget.
  assert.ok(
    cumulativeExpandedLines <= CUMULATIVE_EXPANDED_LINE_BUDGET,
    `expanded ${cumulativeExpandedLines} cumulative lines, exceeding `
    + `the ${CUMULATIVE_EXPANDED_LINE_BUDGET} budget — page would lag`,
  );
});

test('decideAutoExpand keeps per-file rule: a 5K-line file is collapsed regardless of position', () => {
  // Even if first in the list and cumulative budget is fresh, a
  // huge single file MUST collapse.
  const files = [_file(LARGE_FILE_LINE_THRESHOLD + 1)];
  assert.deepEqual(decideAutoExpand(files), [false]);
});

test('decideAutoExpand keeps early files expanded, collapses tail (operator UX)', () => {
  // The operator's normal flow: open Changes tab, read the first
  // few files inline. The fix shouldn't disrupt that — early files
  // expand, only the tail collapses.
  const files = [
    _file(500),    // 500 — fits
    _file(500),    // 1000 — fits
    _file(500),    // 1500 — fits
    _file(500),    // 2000 — exactly at the budget, fits
    _file(500),    // 2500 — over, collapses
    _file(500),    // would push to 3000, collapses
  ];
  const result = decideAutoExpand(files);
  // First four files are inline (totalling 2000 lines, the budget).
  assert.deepEqual(result.slice(0, 4), [true, true, true, true]);
  // The remaining files collapse.
  assert.deepEqual(result.slice(4), [false, false]);
});

test('decideAutoExpand cumulative budget invariant holds for randomized file lists', () => {
  // Property-based: for any reasonable distribution of file sizes,
  // the total lines marked as auto-expanded must never exceed the
  // cumulative budget (modulo the one-file-over-budget threshold).
  // Seeded so test failures are reproducible.
  let seed = 0xDEADBEEF;
  function rand() {
    // Tiny LCG — deterministic across runs.
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  }
  for (let trial = 0; trial < 50; trial += 1) {
    const fileCount = Math.floor(rand() * 30) + 1;
    const files = [];
    for (let i = 0; i < fileCount; i += 1) {
      // Mix of tiny, medium, and very-large files.
      const r = rand();
      let lines;
      if (r < 0.7) {
        lines = Math.floor(rand() * 300) + 10;  // 10-310 (small/medium)
      } else if (r < 0.95) {
        lines = Math.floor(rand() * 400) + 100;  // 100-500 (medium-large)
      } else {
        lines = Math.floor(rand() * 5000) + 600;  // huge
      }
      files.push(_file(lines));
    }
    const expanded = decideAutoExpand(files);
    const totalExpanded = expanded.reduce(
      (sum, isExpanded, i) => sum + (isExpanded ? countDiffLines(files[i]) : 0),
      0,
    );
    assert.ok(
      totalExpanded <= CUMULATIVE_EXPANDED_LINE_BUDGET,
      `trial ${trial}: budget exceeded (${totalExpanded} > ${CUMULATIVE_EXPANDED_LINE_BUDGET}) — `
      + `the auto-collapse rule did not cap cumulative lines`,
    );
  }
});

test('decideAutoExpand cumulative skips count of already-collapsed large files', () => {
  // A huge file collapses (per-file rule), it does NOT consume
  // budget. Subsequent small files still get to expand against the
  // fresh budget — the collapsed file's lines never reach the DOM.
  const files = [
    _file(LARGE_FILE_LINE_THRESHOLD + 1),  // collapses, doesn't eat budget
    _file(500),  // fits in fresh budget (500/2000)
    _file(500),  // fits (1000/2000)
  ];
  assert.deepEqual(decideAutoExpand(files), [false, true, true]);
});
