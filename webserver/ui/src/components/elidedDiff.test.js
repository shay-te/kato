import { test } from 'node:test';
import assert from 'node:assert/strict';

import { isElidedDiff, pickFileDiff } from './elidedDiff.js';

const displayPathOf = (f) => f.newPath || f.oldPath || '';

// Reported: "when i press on the show diff button he shows me a diff of some
// other file". ?full=<path> de-elides one file but returns the WHOLE repo
// diff, so taking parsed[0] rendered whichever file came first.
test('picks the requested file out of a whole-repo diff', () => {
  const files = [
    { newPath: 'a/other.py', hunks: ['other'] },
    { newPath: 'a/admin_form_service.py', hunks: ['wanted'] },
  ];
  const picked = pickFileDiff(files, 'a/admin_form_service.py', displayPathOf);
  assert.deepEqual(picked.hunks, ['wanted']);
});

test('matches on the old path for a deleted file', () => {
  const files = [{ oldPath: 'gone.py', newPath: '/dev/null', hunks: ['x'] }];
  assert.deepEqual(pickFileDiff(files, 'gone.py', displayPathOf).hunks, ['x']);
});

test('returns null rather than the wrong file when absent', () => {
  const files = [{ newPath: 'other.py', hunks: ['other'] }];
  assert.equal(pickFileDiff(files, 'missing.py', displayPathOf), null);
});

test('empty inputs are null, never a stray first file', () => {
  assert.equal(pickFileDiff([{ newPath: 'a.py' }], '', displayPathOf), null);
  assert.equal(pickFileDiff(null, 'a.py', displayPathOf), null);
});

test('detects the server elision placeholder', () => {
  const elided = [{ changes: [{ content: ' (diff too large to display: …)' }] }];
  assert.equal(isElidedDiff(elided), true);
});

test('a real diff quoting the phrase is not an elision', () => {
  const real = [{ changes: [
    { content: '+assert "diff too large to display" in out', isInsert: true },
  ] }];
  assert.equal(isElidedDiff(real), false);
});
