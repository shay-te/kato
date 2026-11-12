// Sub-task rows sit under their main task in the tasks menu.

import test from 'node:test';
import assert from 'node:assert/strict';
import { groupSubtaskTabs, parentOfTab } from './subtaskTabs.js';
import { subtaskListId } from './subtaskTabs.js';

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

test('sub-tasks are grouped under their main task; the rest keep their order', () => {
  const { top, childrenOf } = groupSubtaskTabs([LOCAL_STEP, MAIN, OTHER, TRACKER_STEP]);
  assert.deepEqual(ids(top), ['UNA-1', 'UNA-9']);
  assert.deepEqual(ids(childrenOf.get('UNA-1')), ['LOCAL-4', 'UNA-7']);
  assert.equal(childrenOf.has('UNA-9'), false);
});

test('Tab walks each main task, then its sub-tasks — the order read down the menu', () => {
  const { order } = groupSubtaskTabs([LOCAL_STEP, OTHER, MAIN, TRACKER_STEP]);
  assert.deepEqual(ids(order), ['UNA-9', 'UNA-1', 'LOCAL-4', 'UNA-7']);
});

test('a pinned sub-task keeps its own place, and is not drawn twice', () => {
  const keep = (session) => session.task_id === 'LOCAL-4';
  const { top, childrenOf, order } = groupSubtaskTabs([LOCAL_STEP, MAIN, TRACKER_STEP], { keep });
  assert.deepEqual(ids(top), ['LOCAL-4', 'UNA-1']);
  assert.deepEqual(ids(childrenOf.get('UNA-1')), ['UNA-7']);
  assert.deepEqual(ids(order), ['LOCAL-4', 'UNA-1', 'UNA-7']);
});

test('a sub-task whose main task has no tab here is a row of its own', () => {
  const { top, childrenOf } = groupSubtaskTabs([LOCAL_STEP, OTHER]);
  assert.deepEqual(ids(top), ['LOCAL-4', 'UNA-9']);
  assert.equal(childrenOf.size, 0);
});

test('a sub-task of a sub-task goes under the top main task; a loop groups nothing', () => {
  const nested = tab('LOCAL-8', { plan_parent: 'LOCAL-4' });
  const { top, childrenOf } = groupSubtaskTabs([MAIN, LOCAL_STEP, nested]);
  assert.deepEqual(ids(top), ['UNA-1']);
  assert.deepEqual(ids(childrenOf.get('UNA-1')), ['LOCAL-4', 'LOCAL-8']);
  const a = tab('A', { plan_parent: 'B' });
  const b = tab('B', { plan_parent: 'A' });
  const looped = groupSubtaskTabs([a, b, tab('C', { plan_parent: 'C' })]);
  assert.deepEqual(ids(looped.top), ['A', 'B', 'C']);
  assert.equal(looped.childrenOf.size, 0);
  assert.deepEqual(groupSubtaskTabs(null), { top: [], childrenOf: new Map(), order: [] });
});

test('subtaskListId: one valid element id per main task', () => {
  assert.equal(subtaskListId('UNA-1'), 'tab-subtasks-UNA-1');
  assert.equal(subtaskListId('a b/c'), 'tab-subtasks-a_b_c');
  assert.notEqual(subtaskListId('UNA-1'), subtaskListId('UNA-2'));
});
