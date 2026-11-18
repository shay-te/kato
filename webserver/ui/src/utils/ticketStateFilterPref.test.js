import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import {
  readTicketStateFilter,
  subscribeTicketStateFilter,
  writeTicketStateFilter,
  _resetTicketStateFilter,
} from './ticketStateFilterPref.js';

let _storage = {};

beforeEach(() => {
  _storage = {};
  globalThis.localStorage = {
    getItem: (key) => (key in _storage ? _storage[key] : null),
    setItem: (key, value) => { _storage[key] = String(value); },
    removeItem: (key) => { delete _storage[key]; },
  };
  _resetTicketStateFilter();
});

test('all tasks by default; the state picked is remembered across a reload', () => {
  assert.equal(readTicketStateFilter(), '');
  writeTicketStateFilter('In Review');
  _resetTicketStateFilter();
  assert.equal(readTicketStateFilter(), 'In Review');
});

test('subscribers hear each pick; damaged storage reads as all', () => {
  const seen = [];
  subscribeTicketStateFilter((state) => seen.push(state));
  writeTicketStateFilter('Open');
  writeTicketStateFilter('');
  assert.deepEqual(seen, ['Open', '']);
  _storage['kato.taskStateFilter.v1'] = '{"state": 7}';
  _resetTicketStateFilter();
  assert.equal(readTicketStateFilter(), '');
});
