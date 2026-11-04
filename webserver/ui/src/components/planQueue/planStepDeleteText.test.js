// The words of deleting a plan's sub-task.

import test from 'node:test';
import assert from 'node:assert/strict';
import { deleteStepConfirm, deletedStepToast } from './planStepDeleteText.js';

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
