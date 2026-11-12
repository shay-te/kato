import assert from 'node:assert/strict';
import test from 'node:test';

import { mayLoseWork, unpushedWorkLines } from './unpushedWork.js';


test('mayLoseWork: nothing unpushed and nothing unchecked goes straight on', () => {
  assert.equal(mayLoseWork({ repositories: [], unchecked: [] }), false);
});

test('mayLoseWork: unpushed work in any repository warns', () => {
  assert.equal(mayLoseWork({
    repositories: [{ repository_id: 'backend', changed_files: 1, unpushed_commits: 0 }],
    unchecked: [],
  }), true);
});

test('mayLoseWork: a repository git could not read warns, never "clean"', () => {
  assert.equal(mayLoseWork({ repositories: [], unchecked: [{ repository_id: 'x', error: 'boom' }] }), true);
});

test('mayLoseWork: no answer at all warns', () => {
  assert.equal(mayLoseWork(null), true);
  assert.equal(mayLoseWork(undefined), true);
  assert.equal(mayLoseWork('nope'), true);
});

test('mayLoseWork: missing lists count as empty', () => {
  assert.equal(mayLoseWork({}), false);
});

test('unpushedWorkLines: names commits and files per repository', () => {
  assert.deepEqual(unpushedWorkLines({
    repositories: [
      { repository_id: 'backend', changed_files: 2, unpushed_commits: 3 },
      { repository_id: 'client', changed_files: 1, unpushed_commits: 0 },
      { repository_id: 'docs', changed_files: 0, unpushed_commits: 1 },
    ],
    unchecked: [],
  }), [
    { repository: 'backend', text: '3 commits not pushed, 2 files changed and not committed', unchecked: false },
    { repository: 'client', text: '1 file changed and not committed', unchecked: false },
    { repository: 'docs', text: '1 commit not pushed', unchecked: false },
  ]);
});

test('unpushedWorkLines: an unchecked repository says why', () => {
  assert.deepEqual(unpushedWorkLines({
    repositories: [],
    unchecked: [{ repository_id: 'api', error: 'git did not answer within 15s' }, { repository_id: 'web' }],
  }), [
    { repository: 'api', text: 'could not check: git did not answer within 15s', unchecked: true },
    { repository: 'web', text: 'could not check', unchecked: true },
  ]);
});

test('unpushedWorkLines: no answer has no lines', () => {
  assert.deepEqual(unpushedWorkLines(null), []);
});
