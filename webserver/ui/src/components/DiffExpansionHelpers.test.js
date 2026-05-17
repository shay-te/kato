import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  basePathForDiffFile,
  buildDiffRenderItems,
  expansionRangeForGap,
  pendingCommentExpansions,
  splitSourceLines,
} from './DiffExpansionHelpers.js';

test('splitSourceLines drops only the terminal newline marker', () => {
  assert.deepEqual(splitSourceLines('a\nb\n'), ['a', 'b']);
  assert.deepEqual(splitSourceLines('\n'), ['']);
});

test('basePathForDiffFile reads context from the old side of the diff', () => {
  assert.equal(
    basePathForDiffFile({ oldPath: 'src/old.js', newPath: 'src/new.js' }, 'x'),
    'src/old.js',
  );
  assert.equal(
    basePathForDiffFile({ oldPath: '/dev/null', newPath: 'src/new.js' }, 'src/new.js'),
    'src/new.js',
  );
});

test('buildDiffRenderItems inserts leading, middle, and trailing gaps', () => {
  const hunks = [
    { content: '@@ -5,2 +5,2 @@', oldStart: 5, oldLines: 2, changes: [] },
    { content: '@@ -20,2 +20,2 @@', oldStart: 20, oldLines: 2, changes: [] },
  ];
  const items = buildDiffRenderItems(hunks, 30);
  const gaps = items.filter((item) => {
    return item.type === 'gap';
  });

  assert.deepEqual(
    gaps.map((gap) => [gap.start, gap.end, gap.count]),
    [[1, 5, 4], [7, 20, 13], [22, 31, 9]],
  );
});

test('pendingCommentExpansions windows a comment buried in a middle gap', () => {
  const hunks = [
    { oldStart: 1, oldLines: 3, newStart: 1, newLines: 3, changes: [] },
    { oldStart: 30, oldLines: 3, newStart: 30, newLines: 3, changes: [] },
  ];
  // line 15 is unchanged context hidden in the [4,30) gap (offset 0).
  assert.deepEqual(
    pendingCommentExpansions(hunks, [15], 40),
    [{ start: 12, end: 19 }],
  );
});

test('pendingCommentExpansions inverts the new→old offset from insertions', () => {
  const hunks = [
    // 3 lines inserted: old [1,2] → new [1,5].
    { oldStart: 1, oldLines: 2, newStart: 1, newLines: 5, changes: [] },
    { oldStart: 50, oldLines: 2, newStart: 53, newLines: 2, changes: [] },
  ];
  // Middle gap offset = (1+5)-(1+2) = 3, so new 20 → old 17.
  assert.deepEqual(
    pendingCommentExpansions(hunks, [20], 100),
    [{ start: 14, end: 21 }],
  );
  // Trailing gap carries the same +3 offset: new 60 → old 57.
  assert.deepEqual(
    pendingCommentExpansions(hunks, [60], 100),
    [{ start: 54, end: 61 }],
  );
});

test('pendingCommentExpansions clamps a leading-gap window to line 1', () => {
  const hunks = [
    { oldStart: 10, oldLines: 2, newStart: 10, newLines: 2, changes: [] },
  ];
  assert.deepEqual(
    pendingCommentExpansions(hunks, [4], 40),
    [{ start: 1, end: 8 }],
  );
});

test('pendingCommentExpansions merges adjacent commented windows', () => {
  const hunks = [
    { oldStart: 1, oldLines: 3, newStart: 1, newLines: 3, changes: [] },
    { oldStart: 30, oldLines: 3, newStart: 30, newLines: 3, changes: [] },
  ];
  assert.deepEqual(
    pendingCommentExpansions(hunks, [18, 15], 40),
    [{ start: 12, end: 22 }],
  );
});

test('pendingCommentExpansions ignores lines not inside any gap', () => {
  const hunks = [
    { oldStart: 1, oldLines: 3, newStart: 1, newLines: 3, changes: [] },
    { oldStart: 30, oldLines: 3, newStart: 30, newLines: 3, changes: [] },
  ];
  // line 2 lives inside the first hunk, not a hidden gap.
  assert.deepEqual(pendingCommentExpansions(hunks, [2], 40), []);
  assert.deepEqual(pendingCommentExpansions([], [5], 40), []);
  assert.deepEqual(pendingCommentExpansions(hunks, [], 40), []);
});

test('expansionRangeForGap expands one edge or the whole gap with shift', () => {
  const gap = { start: 10, end: 50 };

  assert.deepEqual(expansionRangeForGap(gap, 'above', false, 5), {
    start: 10,
    end: 15,
  });
  assert.deepEqual(expansionRangeForGap(gap, 'below', false, 5), {
    start: 45,
    end: 50,
  });
  assert.deepEqual(expansionRangeForGap(gap, 'above', true, 5), {
    start: 10,
    end: 50,
  });
});
