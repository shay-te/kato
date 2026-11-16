import { test } from 'node:test';
import assert from 'node:assert/strict';

import { vanishedTaskIds } from './vanishedTasks.js';

test('names the tracked tasks kato no longer lists, once each', () => {
  const sessions = [{ task_id: 'T1' }, { task_id: 'T3' }];
  assert.deepEqual(vanishedTaskIds(['T1', 'T2', 'T2', 'T3', 'T4'], sessions), ['T2', 'T4']);
});

test('an empty or missing list names none: it proves nothing', () => {
  assert.deepEqual(vanishedTaskIds(['T1'], []), []);
  assert.deepEqual(vanishedTaskIds(['T1'], undefined), []);
});

test('nothing tracked, nothing gone; empty ids are never named', () => {
  assert.deepEqual(vanishedTaskIds(undefined, [{ task_id: 'T1' }]), []);
  assert.deepEqual(vanishedTaskIds(['', null], [{ task_id: 'T1' }]), []);
});
