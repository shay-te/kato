// The open-request store a toolbar button uses to ask App for a tab.
import test from 'node:test';
import assert from 'node:assert/strict';

import { createOpenRequests } from './pubsub.js';

test('each open is a new request; a subscriber first gets the current one', () => {
  const requests = createOpenRequests();
  const seen = [];
  const unsubscribe = requests.subscribe((request) => seen.push(request));
  requests.open('UNA-1', { label: 'Plan board' });
  requests.open('UNA-1');
  unsubscribe();
  requests.open('UNA-2');  // nobody listening any more
  assert.deepEqual(seen, [
    { taskId: '', seq: 0 },
    { label: 'Plan board', taskId: 'UNA-1', seq: 1 },
    { taskId: 'UNA-1', seq: 2 },
  ]);
  // A late subscriber gets the latest, and can tell it from a new one by seq.
  const late = [];
  requests.subscribe((request) => late.push(request));
  assert.deepEqual(late, [{ taskId: 'UNA-2', seq: 3 }]);
});
