// Sub-task tabs fold into their main task's tab — the strip stays short.

import test from 'node:test';
import assert from 'node:assert/strict';
import { foldSubtaskTabs, parentOfTab } from './subtaskTabs.js';

const tab = (task_id, extra = {}) => ({ task_id, task_summary: `Name of ${task_id}`, ...extra });
const ids = (sessions) => sessions.map((session) => session.task_id);

const MAIN = tab('UNA-1');
const LOCAL_STEP = tab('LOCAL-4', { plan_parent: 'UNA-1', task_summary: 'UNA-1 · Task 1: The endpoint' });
const TRACKER_STEP = tab('UNA-7', { task_summary: 'Subtask of una-1: The page' });  // a teammate's kato
const OTHER = tab('UNA-9');

test('a sub-task names its main task from the plan queue, else its ticket title', () => {
  assert.equal(parentOfTab(LOCAL_STEP), 'UNA-1');
  assert.equal(parentOfTab(TRACKER_STEP), 'una-1');
  assert.equal(parentOfTab(OTHER), '');
  assert.equal(parentOfTab(null), '');
});

test('sub-tasks fold into their main task; the rest of the strip is untouched', () => {
  const { visible, subtasksOf } = foldSubtaskTabs([LOCAL_STEP, MAIN, OTHER, TRACKER_STEP]);
  assert.deepEqual(ids(visible), ['UNA-1', 'UNA-9']);
  assert.deepEqual(ids(subtasksOf.get('UNA-1')), ['LOCAL-4', 'UNA-7']);
  assert.equal(subtasksOf.has('UNA-9'), false);
});

test('selecting a sub-task keeps the strip short and opens its family in the second row', () => {
  const { visible, subtasksOf, openFamily, strip, order } = foldSubtaskTabs(
    [LOCAL_STEP, OTHER, MAIN, TRACKER_STEP], { activeTaskId: 'UNA-7' },
  );
  assert.deepEqual(ids(visible), ['UNA-9', 'UNA-1']);  // the open sub-task is NOT in the top row
  assert.deepEqual(ids(subtasksOf.get('UNA-1')), ['LOCAL-4', 'UNA-7']);
  assert.equal(openFamily, 'UNA-1');
  assert.deepEqual(ids(strip), ['LOCAL-4', 'UNA-7']);
  assert.deepEqual(ids(order), ['UNA-9', 'UNA-1', 'LOCAL-4', 'UNA-7']);
});

test('selecting the main task opens its sub-tasks in the second row', () => {
  const { openFamily, strip, order } = foldSubtaskTabs([MAIN, LOCAL_STEP, OTHER, TRACKER_STEP], { activeTaskId: 'UNA-1' });
  assert.equal(openFamily, 'UNA-1');
  assert.deepEqual(ids(strip), ['LOCAL-4', 'UNA-7']);
  assert.deepEqual(ids(order), ['UNA-1', 'LOCAL-4', 'UNA-7', 'UNA-9']);
});

test('another task selected, or none: no second row', () => {
  for (const activeTaskId of ['UNA-9', '', 'GONE-1']) {
    const { openFamily, strip, order } = foldSubtaskTabs([MAIN, LOCAL_STEP, OTHER], { activeTaskId });
    assert.equal(openFamily, '');
    assert.deepEqual(strip, []);
    assert.deepEqual(ids(order), ['UNA-1', 'UNA-9']);
  }
});

test('a pinned sub-task keeps its own place, and is not drawn twice', () => {
  const keep = (session) => session.task_id === 'LOCAL-4';
  const { visible, subtasksOf, openFamily, strip } = foldSubtaskTabs(
    [LOCAL_STEP, MAIN, TRACKER_STEP], { keep, activeTaskId: 'LOCAL-4' },
  );
  assert.deepEqual(ids(visible), ['LOCAL-4', 'UNA-1']);
  assert.deepEqual(ids(subtasksOf.get('UNA-1')), ['LOCAL-4', 'UNA-7']);  // still counted
  assert.equal(openFamily, 'UNA-1');  // its family still opens
  assert.deepEqual(ids(strip), ['UNA-7']);
});

test('a sub-task whose main task has no tab here keeps its own', () => {
  const { visible, subtasksOf } = foldSubtaskTabs([LOCAL_STEP, OTHER]);
  assert.deepEqual(ids(visible), ['LOCAL-4', 'UNA-9']);
  assert.equal(subtasksOf.size, 0);
});

test('a sub-task of a sub-task folds into the top main task; a loop folds nothing', () => {
  const nested = tab('LOCAL-8', { plan_parent: 'LOCAL-4' });
  const { visible, subtasksOf } = foldSubtaskTabs([MAIN, LOCAL_STEP, nested]);
  assert.deepEqual(ids(visible), ['UNA-1']);
  assert.deepEqual(ids(subtasksOf.get('UNA-1')), ['LOCAL-4', 'LOCAL-8']);
  const a = tab('A', { plan_parent: 'B' });
  const b = tab('B', { plan_parent: 'A' });
  const looped = foldSubtaskTabs([a, b, tab('C', { plan_parent: 'C' })]);
  assert.deepEqual(ids(looped.visible), ['A', 'B', 'C']);
  assert.equal(looped.subtasksOf.size, 0);
  assert.deepEqual(foldSubtaskTabs(null), {
    visible: [], subtasksOf: new Map(), openFamily: '', strip: [], order: [],
  });
});
