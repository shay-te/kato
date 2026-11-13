import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  emptyBackgroundJobs,
  hasBackgroundJobs,
  hasBackgroundWorkflow,
  observeBackgroundEvent,
  outstandingBackgroundJobs,
  outstandingFromEvents,
} from './backgroundJobs.js';

// The SAME cases the server runs (tests/test_background_jobs_parity.py).
const CASES = JSON.parse(readFileSync(
  new URL('../../../../tests/fixtures/background_jobs_cases.json', import.meta.url), 'utf8',
)).cases;

for (const testCase of CASES) {
  test(`shared case: ${testCase.name}`, () => {
    assert.deepEqual(outstandingFromEvents(testCase.events), testCase.outstanding);
  });
}

const call = (id, name, input = {}) => ({
  type: 'assistant', message: { content: [{ type: 'tool_use', id, name, input }] },
});

test('a Workflow is flagged as a workflow until it reports back', () => {
  let state = observeBackgroundEvent(emptyBackgroundJobs(), call('w', 'Workflow'));
  state = observeBackgroundEvent(state, call('m', 'Monitor'));
  assert.equal(hasBackgroundWorkflow(state), true);
  state = observeBackgroundEvent(state, {
    type: 'system', subtype: 'task_notification', tool_use_id: 'w', status: 'completed',
  });
  assert.equal(hasBackgroundWorkflow(state), false);
  assert.equal(hasBackgroundJobs(state), true);
  assert.deepEqual(outstandingBackgroundJobs(state), ['m']);
});

test('observing never changes the state it was given', () => {
  const before = observeBackgroundEvent(emptyBackgroundJobs(), call('a', 'Monitor'));
  const frozen = JSON.stringify(before);
  observeBackgroundEvent(before, { type: 'user', message: { content: '<task-notification>' } });
  assert.equal(JSON.stringify(before), frozen);
});

test('a missing or odd state reads as nothing running', () => {
  assert.equal(hasBackgroundJobs(undefined), false);
  assert.equal(hasBackgroundWorkflow(null), false);
  assert.deepEqual(outstandingBackgroundJobs({}), []);
  assert.deepEqual(outstandingFromEvents(undefined), []);
  const state = observeBackgroundEvent(undefined, call('a', 'Monitor'));
  assert.deepEqual(outstandingBackgroundJobs(state), ['a']);
  assert.equal(observeBackgroundEvent(state, null), state);
  assert.equal(observeBackgroundEvent(state, ['x']), state);
});

test('an id that names an Object property is an ordinary job', () => {
  // ``'constructor' in {}`` is true — membership must look at own keys only.
  const state = observeBackgroundEvent(emptyBackgroundJobs(), call('constructor', 'Monitor'));
  assert.deepEqual(outstandingBackgroundJobs(state), ['constructor']);
  const started = observeBackgroundEvent(emptyBackgroundJobs(), {
    type: 'system', subtype: 'task_started', task_id: 't', tool_use_id: 'toString', is_backgrounded: false,
  });
  assert.deepEqual(outstandingBackgroundJobs(started), []);
});

test('missing names, ids and statuses still follow the rule', () => {
  const state = [
    // a background call with no tool name
    { type: 'assistant', message: { content: [{ type: 'tool_use', id: 'n', input: { run_in_background: true } }] } },
    // the CLI starting a task with no tool_use id: followed by its task id
    { type: 'system', subtype: 'task_started', task_id: 't3', is_backgrounded: true },
    // a patch with no status ends nothing
    { type: 'system', subtype: 'task_updated', task_id: 't3', patch: {} },
  ].reduce(observeBackgroundEvent, emptyBackgroundJobs());
  assert.deepEqual(outstandingBackgroundJobs(state), ['n', 't3']);
  assert.equal(hasBackgroundWorkflow(state), false);
});

test('odd shapes inside events are skipped, not thrown on', () => {
  const events = [
    { type: 'assistant', message: { content: ['x', null, { type: 'text' }, { type: 'tool_use', name: 'Bash', input: ['no'] }] } },
    { type: 'assistant', message: { content: 'text' } },
    { type: 'system', subtype: 'task_updated', task_id: 't', patch: ['no'] },
    { type: 'system', subtype: 'task_updated', task_id: 't' },
    { type: 'system', subtype: 'background_tasks_changed', tasks: ['x', null, [], { task_id: '' }] },
    { type: 'system', subtype: 'background_tasks_changed', tasks: 'not a list' },
    { type: 'user', message: { content: [null, 'x', { type: 'text' }, { type: 'image' }] } },
    { type: 'user', message: null },
    { type: 'user' },
  ];
  assert.deepEqual(outstandingFromEvents(events), []);
});
