// The words of deleting a plan's sub-task.

import test from 'node:test';
import assert from 'node:assert/strict';
import { deleteStepConfirm, deletedStepToast, forgottenSubTasksToast } from './planStepDeleteText.js';

test('the question says everything that goes', () => {
  const text = deleteStepConfirm({ id: 'api', task_id: 'UNA-3376' });
  assert.match(text, /^Delete UNA-3376\?/);
  assert.match(text, /pull requests are declined/);
  assert.match(text, /folder, chat and containers are deleted/);
  assert.match(deleteStepConfirm({ id: 'api', task_id: '' }), /^Delete api\?/);
});

test('what happened: declined, deleted, out of the plan — a folder that stayed comes first', () => {
  const done = deletedStepToast('UNA-3376', { task_id: 'UNA-3376', declined: ['https://git/pull/7'] }, { ok: true });
  assert.deepEqual(done, {
    kind: 'success', title: 'Deleted UNA-3376', taskId: 'UNA-3376',
    message: '✓ declined https://git/pull/7\n✓ its folder, chat and containers deleted\n✓ taken out of the plan',
  });
  const stuck = deletedStepToast('UNA-3376', { task_id: 'UNA-3376', declined: [] },
    { ok: false, body: { error: 'a file is locked' } });
  assert.equal(stuck.kind, 'warning');
  assert.match(stuck.message, /^⚠ its folder was not deleted: a file is locked\n✓ taken out of the plan$/);
  assert.match(deletedStepToast('x', {}, { ok: false }).message, /see the kato log/);
  assert.equal(deletedStepToast('api', { task_id: '' }, null).message, '✓ taken out of the plan');
  assert.equal(deletedStepToast('api', undefined, null).taskId, '');
});

test('forgetting a task cascades to its sub-tasks: nothing to say is no toast', () => {
  assert.equal(forgottenSubTasksToast([]), null);
  assert.equal(forgottenSubTasksToast(undefined), null);
});

test('every sub-task forgotten cleanly is one success toast', () => {
  const result = forgottenSubTasksToast([
    { task_id: 'T-101', forgotten: true, error: '', declined: [], decline_problems: [] },
    { task_id: 'T-102', forgotten: true, error: '', declined: [], decline_problems: [] },
  ]);
  assert.deepEqual(result, {
    kind: 'success', title: '2 sub-tasks forgotten too',
    message: '✓ T-101\n✓ T-102',
  });
});

test('one sub-task, singular wording', () => {
  const result = forgottenSubTasksToast([{ task_id: 'T-101', forgotten: true }]);
  assert.equal(result.title, '1 sub-task forgotten too');
});

test('a sub-task that did not forget is a warning naming the count and the reason', () => {
  const result = forgottenSubTasksToast([
    { task_id: 'T-101', forgotten: true },
    { task_id: 'T-102', forgotten: false, error: 'workspace directory still exists' },
  ]);
  assert.equal(result.kind, 'warning');
  assert.equal(result.title, '1/2 sub-tasks forgotten');
  assert.match(result.message, /⚠ T-102: workspace directory still exists/);
});

test('an un-declined pull request is flagged even though the sub-task still forgot', () => {
  const result = forgottenSubTasksToast([
    { task_id: 'T-101', forgotten: true, decline_problems: ['api: its pull request could not be declined — 403'] },
  ]);
  assert.equal(result.kind, 'warning');
  assert.match(result.message, /✓ T-101: api: its pull request could not be declined — 403/);
});
