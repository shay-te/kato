import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  basePathForDiffFile,
  buildDiffRenderItems,
  expansionRangeForGap,
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
