// Filename matches render ABOVE content matches.
//
// A 200-row grep dump used to sit on top of the tree, pushing the file the
// operator was looking for off screen. Searching for a file is the common
// case; content matches are the fallback and now read as one.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const source = readFileSync(
  join(new URL('.', import.meta.url).pathname, 'FilesTab.jsx'), 'utf8',
);

test('the tree is rendered before the content results', () => {
  const body = source.indexOf('{body}');
  const content = source.indexOf('{contentResults}');
  assert.notEqual(body, -1, 'the tree is no longer rendered');
  assert.notEqual(content, -1, 'the content results are no longer rendered');
  assert.ok(
    body < content,
    'content matches must come AFTER the filename matches — they are the '
    + 'fallback, and on top they push the tree off screen',
  );
});

test('the scope reaches BOTH halves of the results', () => {
  // A scope applied to only the tree, or only the grep hits, would be worse
  // than none: the operator would still scroll past another repo.
  assert.match(source, /scopedTrees/);
  assert.match(source, /scopeRepoId=\{scopeRepoId\}/);
});

test('the picker only appears when there is more than one repo', () => {
  assert.match(source, /trees\.length > 1 && \(/);
});

test('the tree height follows the VISIBLE rows, not the root count', () => {
  assert.match(source, /countVisibleTreeRows\(treeData, isFiltering/);
  assert.doesNotMatch(source, /treeData\.length \* 28/);
});
