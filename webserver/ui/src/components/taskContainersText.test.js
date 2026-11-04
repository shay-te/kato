// The words of a task's containers chip and of what its actions did.

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  containersChipText, containersOutcomeToast, forgottenContainersToast, removeContainersConfirm,
} from './taskContainersText.js';

const running = { name: 'db', running: true };
const stopped = { name: 'web', running: false };

test('the chip says how many run and how many are stopped', () => {
  assert.equal(containersChipText([running, running]), '2 running');
  assert.equal(containersChipText([running, stopped, stopped]), '1 running · 2 stopped');
  assert.equal(containersChipText([stopped]), '1 stopped');
});

test('removing asks first and says what data goes', () => {
  assert.match(removeContainersConfirm('UNA-1', 2), /Remove the 2 containers UNA-1's agent started\?/);
  assert.match(removeContainersConfirm('UNA-1', 1), /the 1 container UNA-1/);
  assert.match(removeContainersConfirm('UNA-1', 1), /Data kept only in those volumes is lost/);
});

test('an action reports what failed first, then what it did', () => {
  const done = containersOutcomeToast('remove', { ok: true, body: {
    containers: ['db'], networks: ['una-1_default'], volumes: ['una-1_pg'], problems: [],
  } }, 'UNA-1');
  assert.deepEqual(done, {
    kind: 'success', title: 'Containers removed', taskId: 'UNA-1',
    message: '✓ db\n✓ network una-1_default\n✓ volume una-1_pg',
  });
  const partly = containersOutcomeToast('stop', { ok: false, body: { containers: ['db'], problems: ['web: denied'] } }, 'UNA-1');
  assert.equal(partly.kind, 'warning');
  assert.equal(partly.message, '⚠ web: denied\n✓ db');
  assert.equal(containersOutcomeToast('stop', { ok: false, body: { problems: ['no docker'] } }).kind, 'error');
  assert.deepEqual(containersOutcomeToast('stop', { ok: false, error: 'offline' }, 'UNA-1'),
    { kind: 'error', title: 'Containers not stopped', message: 'offline', taskId: 'UNA-1' });
  assert.equal(containersOutcomeToast('stop', null).message, 'kato did not answer');
  assert.equal(containersOutcomeToast('stop', { ok: true, body: {} }).title, 'Nothing to stop');
  assert.equal(containersOutcomeToast('remove', { ok: true, body: {} }).title, 'Nothing to remove');
});

test('a deleted task says what happened to its containers only when something did', () => {
  assert.equal(forgottenContainersToast('UNA-1', undefined), null);
  assert.equal(forgottenContainersToast('UNA-1', { containers: [], problems: [] }), null);
  assert.equal(forgottenContainersToast('UNA-1', { containers: ['db'], problems: [] }).kind, 'success');
  assert.equal(forgottenContainersToast('UNA-1', { containers: [], problems: ['busy'] }).kind, 'error');
});
