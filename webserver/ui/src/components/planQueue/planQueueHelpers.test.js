import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  BOARD_COLUMNS,
  PLAN_STATUS_TEXT,
  boardColumns,
  buttonDot,
  buttonLabel,
  isBoardStatus,
  isFollowed,
  columnOf,
  headerActions,
  isStuck,
  planSummaryFor,
  prChips,
  repoCoverage,
  roundsSummary,
  planStatusLine,
  progressText,
  stepCode,
  stepNote,
  stepRounds,
  stepWorker,
  stuckActions,
  withRepoEverywhere,
} from './planQueueHelpers.js';
import { planQueueChanges, planQueueView } from './planQueueViewStore.js';

const step = (status, extra = {}) => ({ id: 's', status, reason: '', reason_code: '', released_at: 0, ...extra });

test('every live status lands in its column', function () {
  const expected = {
    blocked: 'waiting', ready: 'waiting', starting: 'progress', active: 'progress',
    in_review: 'review', awaiting_merge: 'merge', satisfied: 'done',
  };
  for (const [status, column] of Object.entries(expected)) {
    assert.equal(columnOf(step(status)), column, status);
  }
  assert.deepEqual(BOARD_COLUMNS.map((c) => c.label),
    ['Waiting', 'In progress', 'In review', 'Waiting for merge', 'Done']);
});

test('a failed or cancelled step stays where it stopped', function () {
  assert.equal(columnOf(step('failed', { reason_code: 'pr_declined' })), 'merge');
  assert.equal(columnOf(step('failed', { reason_code: 'task_gone' })), 'progress');
  assert.equal(columnOf(step('failed', { reason_code: 'create_failed' })), 'waiting');
  assert.equal(columnOf(step('failed', { reason_code: 'something new' })), 'waiting');
  assert.equal(columnOf(step('cancelled')), 'waiting');
  assert.equal(columnOf(step('cancelled', { released_at: 5 })), 'progress');
});

test('the board groups a plan by column', function () {
  const columns = boardColumns({ steps: [step('ready', { id: 'a' }), step('satisfied', { id: 'b' })] });
  assert.deepEqual(columns.map((c) => c.steps.map((s) => s.id)), [['a'], [], [], [], ['b']]);
  assert.equal(boardColumns(null).every((c) => c.steps.length === 0), true);
});

test('a card shows its task id once it has one', function () {
  assert.equal(stepCode({ id: 'api', task_id: 'UNA-1235' }), 'UNA-1235');
  assert.equal(stepCode({ id: 'api', task_id: '' }), 'api');
});

test('only a stuck card offers anything', function () {
  assert.deepEqual(stuckActions(step('active')), []);
  assert.deepEqual(stuckActions(step('failed')).map((a) => a.action), ['retry', 'satisfy']);
  const noPr = step('awaiting_merge', { reason: 'no pull request found — no changes? Count it as done' });
  assert.equal(isStuck(noPr), true);
  assert.deepEqual(stuckActions(noPr).map((a) => a.action), ['satisfy']);
  assert.equal(isStuck(step('awaiting_merge', { reason: 'waiting for the merge' })), false);
});

test('notes and pull request chips', function () {
  assert.equal(stepNote(step('cancelled', { reason: 'x' })), 'cancelled');
  assert.equal(stepNote(step('blocked', { reason: 'waits for UNA-1' })), 'waits for UNA-1');
  assert.equal(stepNote({ status: 'active' }), '');
  const chips = prChips({ merge: { repos: [
    { repository_id: 'api', state: 'merged', url: 'u' },
    { repository_id: 'web', state: 'unknown' },
    { repository_id: 'ops', state: 'none' },
  ] } });
  assert.deepEqual(chips, [
    { repository: 'api', state: 'merged', url: 'u' },
    { repository: 'ops', state: 'none', url: '' },
  ]);
  assert.deepEqual(prChips({}), []);
});

test('header controls follow the plan state', function () {
  assert.deepEqual(headerActions('approved'), ['run', 'stop']);
  assert.deepEqual(headerActions('paused'), ['run', 'stop']);
  assert.deepEqual(headerActions('running'), ['pause', 'stop']);
  assert.deepEqual(headerActions('finished'), []);
});

test('the summary for a task prefers an unfinished plan, then the newest', function () {
  const plans = [
    { plan_id: 'old', source_task_id: 'UNA-1', status: 'finished', updated_at: 9 },
    { plan_id: 'live', source_task_id: 'una-1', status: 'running', updated_at: 5 },
    { plan_id: 'other', source_task_id: 'UNA-2', status: 'running', updated_at: 99 },
  ];
  assert.equal(planSummaryFor(plans, 'UNA-1').plan_id, 'live');
  assert.equal(planSummaryFor(plans.slice(0, 1), 'UNA-1').plan_id, 'old');
  assert.equal(planSummaryFor(plans, 'UNA-3'), null);
  assert.equal(planSummaryFor(null, 'UNA-1'), null);
});

test('button label and progress', function () {
  assert.equal(buttonLabel(null), 'Split plan');
  assert.equal(buttonLabel({ status: 'cancelled' }), 'Split plan');
  assert.equal(buttonLabel({ status: 'running' }), 'Plan board');
  for (const status of ['proposing', 'propose_failed', 'proposed']) {
    assert.equal(buttonLabel({ status }), 'Split plan', status);  // no board yet
  }
  assert.equal(progressText({ step_count: 5, counts: { satisfied: 3 } }), '3/5 done');
  assert.equal(progressText({ step_count: 2, counts: {} }), '0/2 done');
  assert.equal(progressText({ step_count: 0 }), '');
  assert.equal(planStatusLine({ status: 'running', step_count: 17, counts: { satisfied: 3 } }), 'Running · 3/17 done');
  assert.equal(planStatusLine({ status: 'proposing', step_count: 0 }), PLAN_STATUS_TEXT.proposing);
  assert.equal(planStatusLine({ status: 'odd', step_count: 0 }), 'odd');
  assert.equal(planStatusLine(null), '');
});

test('the view store tells its subscriber which task to open, and the tab\'s name', function () {
  const seen = [];
  const unsubscribe = planQueueView.subscribe((request) => seen.push([request.taskId, request.label]));
  planQueueView.open('UNA-7', 'Plan board');
  planQueueView.open();
  unsubscribe();
  assert.deepEqual(seen.slice(-2), [['UNA-7', 'Plan board'], ['', '']]);
});

test('a plan change is announced with its task', function () {
  const seen = [];
  const unsubscribe = planQueueChanges.subscribe((change) => seen.push(change));
  planQueueChanges.notify('UNA-7');
  planQueueChanges.notify();
  unsubscribe();
  assert.deepEqual(seen.slice(-2).map((change) => change.taskId), ['UNA-7', '']);
  assert.ok(seen.at(-1).seq > seen.at(-2).seq);
});

// The single-agent-status rule (CLAUDE.md): the plan queue renders its OWN
// axis and must never read a session's working flag or the agent status kinds.
test('no plan queue source derives an agent status', function () {
  const here = dirname(fileURLToPath(import.meta.url));
  const sources = readdirSync(here).filter((name) => /\.jsx?$/.test(name) && !name.includes('.test.'));
  assert.ok(sources.length >= 3);
  for (const name of sources) {
    const text = readFileSync(join(here, name), 'utf8');
    assert.equal(/\.working\b|AGENT_STATUS_KIND/.test(text), false, name);
  }
});


test('the button dot: running while proposing or running, amber while a split waits', () => {
  assert.equal(buttonDot({ status: 'proposing' }), 'running');
  assert.equal(buttonDot({ status: 'running' }), 'running');
  assert.equal(buttonDot({ status: 'proposed' }), 'warn');
  for (const status of ['approved', 'paused', 'finished', 'cancelled', 'propose_failed']) {
    assert.equal(buttonDot({ status }), '', status);
  }
  assert.equal(buttonDot(null), '');
});

test('the board shows once the proposal is behind the plan', () => {
  for (const status of ['proposing', 'propose_failed', 'proposed', '', undefined]) {
    assert.equal(isBoardStatus(status), false, String(status));
  }
  for (const status of ['approved', 'running', 'paused', 'finished', 'cancelled']) {
    assert.equal(isBoardStatus(status), true, status);
  }
});


test('who has a step, while it is someone\'s to do', () => {
  assert.equal(stepWorker({ worker: 'Dana', status: 'active' }), 'with Dana');
  assert.equal(stepWorker({ worker: 'Dana', status: 'satisfied' }), '');
  assert.equal(stepWorker({ worker: '', status: 'active' }), '');
  assert.equal(isFollowed({ mode: 'followed' }), true);
  assert.equal(isFollowed({ mode: 'tracker' }), false);
  assert.equal(isFollowed(null), false);
});

test('a sub-task\'s own tab finds the plan it is part of', () => {
  const plans = [{ plan_id: 'p', source_task_id: 'UNA-1', status: 'running', task_ids: ['UNA-2', 'UNA-3'] }];
  assert.equal(planSummaryFor(plans, 'una-3')?.plan_id, 'p');
  assert.equal(planSummaryFor(plans, 'UNA-9'), null);
  assert.equal(planSummaryFor([{ plan_id: 'q', source_task_id: 'UNA-1' }], 'UNA-1')?.plan_id, 'q');
});

test('rounds: the order the queue runs the steps in', () => {
  const steps = [
    { id: 'a', depends_on: [] },
    { id: 'b', depends_on: ['a'] },
    { id: 'c', depends_on: ['a', 'b'] },
    { id: 'd', depends_on: ['gone'] },
    { id: 'x', depends_on: ['y'] },
    { id: 'y', depends_on: ['x'] },
  ];
  const rounds = stepRounds(steps);
  assert.deepEqual(Object.fromEntries(rounds), { a: 1, b: 2, c: 3, d: 1, x: 0, y: 0 });
  assert.equal(roundsSummary(steps, rounds), '6 tasks · 3 rounds · 2 start right away');
  const one = [{ id: 'a', depends_on: [] }];
  assert.equal(roundsSummary(one, stepRounds(one)), '1 task · 1 round · 1 starts right away');
  assert.equal(roundsSummary([], stepRounds([])), '0 tasks');
  assert.equal(stepRounds(null).size, 0);
});


test('every task works in a repository — or some, or none', () => {
  const steps = [
    { id: 'a', repositories: ['api', 'core'] },
    { id: 'b', repositories: ['web'] },
  ];
  assert.deepEqual(repoCoverage(steps, 'core'), { state: 'some', count: 1 });
  assert.deepEqual(repoCoverage(steps, 'docs'), { state: 'none', count: 0 });
  const everywhere = withRepoEverywhere(steps, 'core', true);
  assert.deepEqual(everywhere.map((step) => step.repositories), [['api', 'core'], ['web', 'core']]);
  assert.deepEqual(repoCoverage(everywhere, 'core'), { state: 'all', count: 2 });
  const nowhere = withRepoEverywhere(everywhere, 'core', false);
  assert.deepEqual(nowhere.map((step) => step.repositories), [['api'], ['web']]);
  assert.deepEqual(repoCoverage([], 'core'), { state: 'none', count: 0 });
  assert.deepEqual(withRepoEverywhere(null, 'core', true), []);
  assert.equal(steps[1].repositories.length, 1);  // the input is not changed
});
