import { test } from 'node:test';
import assert from 'node:assert/strict';

import { taskBranchOfItsOwn } from './taskBranch.js';

test('a branch of its own is named', () => {
  assert.equal(taskBranchOfItsOwn({ task_id: 'LOCAL-3', branch_name: 'feature/retry' }), 'feature/retry');
});

test('a branch named after the task, or none, is not', () => {
  assert.equal(taskBranchOfItsOwn({ task_id: 'LOCAL-3', branch_name: 'LOCAL-3' }), '');
  assert.equal(taskBranchOfItsOwn({ task_id: 'LOCAL-3' }), '');
  assert.equal(taskBranchOfItsOwn({ task_id: 'LOCAL-3', branch_name: '  ' }), '');
  assert.equal(taskBranchOfItsOwn(null), '');
});
