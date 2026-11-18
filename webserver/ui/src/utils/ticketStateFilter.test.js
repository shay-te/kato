import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ALL_TICKET_STATES,
  NO_TICKET_STATE,
  filterByTicketState,
  hasTicketStates,
  ticketStateOptions,
} from './ticketStateFilter.js';

const sessions = [
  { task_id: 'UNA-1', ticket_state: 'In Review' },
  { task_id: 'UNA-2', ticket_state: 'In Progress' },
  { task_id: 'UNA-3', ticket_state: 'In Review' },
  { task_id: 'GH-4', ticket_state: '' },
];

test('the dropdown offers All, each state the tasks are in, then No status — with counts', () => {
  assert.deepEqual(ticketStateOptions(sessions), [
    { value: ALL_TICKET_STATES, label: 'All', count: 4 },
    { value: 'In Progress', label: 'In Progress', count: 1 },
    { value: 'In Review', label: 'In Review', count: 2 },
    { value: NO_TICKET_STATE, label: 'No status', count: 1 },
  ]);
});

test('the state picked stays offered when no task is in it any more', () => {
  const options = ticketStateOptions(sessions, 'Done');
  assert.deepEqual(options.find((option) => option.value === 'Done'), { value: 'Done', label: 'Done', count: 0 });
});

test('All keeps every task; a state keeps its tasks; No status keeps those with none', () => {
  assert.equal(filterByTicketState(sessions, ALL_TICKET_STATES), sessions);
  assert.deepEqual(filterByTicketState(sessions, 'In Review').map((s) => s.task_id), ['UNA-1', 'UNA-3']);
  assert.deepEqual(filterByTicketState(sessions, NO_TICKET_STATE).map((s) => s.task_id), ['GH-4']);
});

test('the task on screen always stays, whatever its state', () => {
  assert.deepEqual(
    filterByTicketState(sessions, 'In Review', { keep: 'UNA-2' }).map((s) => s.task_id),
    ['UNA-1', 'UNA-2', 'UNA-3'],
  );
});

test('the dropdown is worth showing once a state is known, or one is picked', () => {
  assert.equal(hasTicketStates(sessions), true);
  assert.equal(hasTicketStates([{ task_id: 'GH-1' }]), false);
  assert.equal(hasTicketStates([{ task_id: 'GH-1' }], 'In Review'), true);
  assert.equal(hasTicketStates(null), false);
  assert.deepEqual(filterByTicketState(null, 'x'), []);
});
