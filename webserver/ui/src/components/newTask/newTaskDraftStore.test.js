// The draft survives a reload: it lives in localStorage, shimmed here the way
// the other preference tests do.
import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import {
  _resetNewTaskDraft,
  discardNewTaskDraft,
  openNewTaskDraft,
  readNewTaskDraft,
  updateNewTaskDraft,
} from './newTaskDraftStore.js';

const KEY = 'kato.newTaskDraft.v1';
let stored = {};

beforeEach(() => {
  stored = {};
  globalThis.localStorage = {
    getItem: (key) => (key in stored ? stored[key] : null),
    setItem: (key, value) => { stored[key] = String(value); },
    removeItem: (key) => { delete stored[key]; },
  };
  _resetNewTaskDraft();
});

test('starts closed, empty, and on Plan', () => {
  const draft = readNewTaskDraft();
  assert.equal(draft.open, false);
  assert.equal(draft.startMode, 'plan');
  assert.deepEqual(draft.repositories, []);
});

test('what is typed is saved and read back after a reload', () => {
  openNewTaskDraft();
  updateNewTaskDraft({ title: 'Add retry', repositories: ['api'] });
  _resetNewTaskDraft(); // a reload: the module cache is gone, localStorage is not
  const draft = readNewTaskDraft();
  assert.equal(draft.open, true);
  assert.equal(draft.title, 'Add retry');
  assert.deepEqual(draft.repositories, ['api']);
  assert.equal(JSON.parse(stored[KEY]).title, 'Add retry');
});

test('discarding closes the tab and forgets the text', () => {
  openNewTaskDraft();
  updateNewTaskDraft({ title: 'x', startMode: 'implement' });
  discardNewTaskDraft();
  const draft = readNewTaskDraft();
  assert.equal(draft.open, false);
  assert.equal(draft.title, '');
  assert.equal(draft.startMode, 'plan');
});

test('garbage in storage reads as an empty draft', () => {
  stored[KEY] = '{not json';
  _resetNewTaskDraft();
  assert.equal(readNewTaskDraft().startMode, 'plan');
});
