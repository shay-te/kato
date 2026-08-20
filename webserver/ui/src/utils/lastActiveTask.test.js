// Which task tab the operator was last looking at.
//
// Reopening kato — a refresh, or relaunching the desktop app — dropped it
// and landed on nothing, so the operator had to find their task in the
// strip again every time.

import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import {
  clearLastActiveTask,
  readLastActiveTask,
  writeLastActiveTask,
  _resetLastActiveTask,
} from './lastActiveTask.js';

let _store = {};

function installLocalStorage() {
  globalThis.localStorage = {
    getItem: (k) => (k in _store ? _store[k] : null),
    setItem: (k, v) => { _store[k] = String(v); },
    removeItem: (k) => { delete _store[k]; },
  };
}

beforeEach(() => {
  _store = {};
  installLocalStorage();
  _resetLastActiveTask();
});

test('nothing remembered on a first run', () => {
  assert.equal(readLastActiveTask(), '');
});

test('a selection survives a reload', () => {
  writeLastActiveTask('UNA-2818');
  _resetLastActiveTask();          // fresh module cache, as after a reload
  assert.equal(readLastActiveTask(), 'UNA-2818');
});

test('selecting another task replaces it', () => {
  writeLastActiveTask('UNA-1');
  writeLastActiveTask('UNA-2');
  assert.equal(readLastActiveTask(), 'UNA-2');
});

test('clearing leaves nothing to restore', () => {
  // Forgetting the task the operator was on must not leave it as the one
  // to reopen — the next launch would try to restore a task that is gone.
  writeLastActiveTask('UNA-1');
  clearLastActiveTask();
  assert.equal(readLastActiveTask(), '');
});

test('whitespace is trimmed rather than stored', () => {
  writeLastActiveTask('  UNA-1  ');
  assert.equal(readLastActiveTask(), 'UNA-1');
});

test('a non-string value does not corrupt the stored id', () => {
  writeLastActiveTask(null);
  assert.equal(readLastActiveTask(), '');
  writeLastActiveTask(undefined);
  assert.equal(readLastActiveTask(), '');
});

test('a corrupt stored value reads as nothing remembered', () => {
  _store['kato.lastActiveTask.v1'] = '{not json';
  _resetLastActiveTask();
  assert.equal(readLastActiveTask(), '');
});
