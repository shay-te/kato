import { test } from 'node:test';
import assert from 'node:assert/strict';

import { forgottenByDelete, forgottenTasks } from './forgottenTaskStore.js';

function listen() {
  const calls = [];
  const stop = forgottenTasks.subscribe((ids) => calls.push(ids));
  return { calls, stop };
}

test('tells every listener which tasks went, once each, never an empty id', () => {
  const first = listen();
  const second = listen();
  forgottenTasks.announce(['T1', '', 'T1', null, 'T2']);
  assert.deepEqual(first.calls, [['T1', 'T2']]);
  assert.deepEqual(second.calls, [['T1', 'T2']]);
  first.stop();
  second.stop();
});

test('says nothing for nothing, and nothing to a listener that left', () => {
  const { calls, stop } = listen();
  forgottenTasks.announce([]);
  forgottenTasks.announce(undefined);
  assert.deepEqual(calls, []);
  stop();
  forgottenTasks.announce(['T1']);
  assert.deepEqual(calls, []);
});

test('a delete is an event: a late listener is not told about an earlier one', () => {
  forgottenTasks.announce(['T1']);
  const { calls, stop } = listen();
  assert.deepEqual(calls, []);
  stop();
});

test('one listener that throws does not stop the others', () => {
  const stopBad = forgottenTasks.subscribe(() => { throw new Error('boom'); });
  const good = listen();
  forgottenTasks.announce(['T1']);
  assert.deepEqual(good.calls, [['T1']]);
  stopBad();
  good.stop();
});

test('a delete that went through: the task and every sub-task really forgotten with it', () => {
  assert.deepEqual(forgottenByDelete('UNA-1', {
    ok: true,
    body: { sub_tasks: [
      { task_id: 'UNA-2', forgotten: true },
      { task_id: 'UNA-3', forgotten: false, error: 'locked' },  // still there: keeps its tabs
      { task_id: 'UNA-4', forgotten: true, decline_problems: ['403'] },
    ] },
  }), ['UNA-1', 'UNA-2', 'UNA-4']);
});

test('a failed delete keeps the task, but sub-tasks it took first are gone', () => {
  assert.deepEqual(forgottenByDelete('UNA-1', {
    ok: false, body: { sub_tasks: [{ task_id: 'UNA-2', forgotten: true }] },
  }), ['UNA-2']);
  assert.deepEqual(forgottenByDelete('UNA-1', { ok: false, error: 'boom' }), []);
});

test('no sub-tasks: just the task; no answer: nothing', () => {
  assert.deepEqual(forgottenByDelete('UNA-1', { ok: true }), ['UNA-1']);
  assert.deepEqual(forgottenByDelete('UNA-1', null), []);
});
