// Tests for the "ultracode by default" preference and how it composes with
// the PER-TASK chip. Pure localStorage pref (no backend); localStorage is
// shimmed the same way composerSteerPref.test does.

import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import {
  readUltracodeByDefault,
  writeUltracodeByDefault,
  subscribeUltracodeByDefault,
  _resetUltracodeDefaultPref,
} from './ultracodeDefaultPref.js';
import { readUltracode, writeUltracode } from './composerDraft.js';

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
  _resetUltracodeDefaultPref();
});

test('defaults to OFF — workflow mode fans out and costs real tokens', () => {
  assert.equal(readUltracodeByDefault(), false);
});

test('a flip persists across a reload', () => {
  writeUltracodeByDefault(true);
  assert.equal(readUltracodeByDefault(), true);
  _resetUltracodeDefaultPref();
  assert.equal(readUltracodeByDefault(), true);
});

test('subscribers see the new value', () => {
  const seen = [];
  const unsubscribe = subscribeUltracodeByDefault((value) => seen.push(value));
  writeUltracodeByDefault(true);
  assert.deepEqual(seen, [true]);
  unsubscribe();
  writeUltracodeByDefault(false);
  assert.deepEqual(seen, [true]);
});

test('a task that was never toggled takes the default', () => {
  assert.equal(readUltracode('T-1', undefined, true), true);
  assert.equal(readUltracode('T-1', undefined, false), false);
});

test('an explicit OFF is not re-armed when the default is later turned on', () => {
  // This is why the per-task value is stored as 'off' rather than '':
  // "I turned this off for this task" and "I never chose" have to be
  // distinguishable, or flipping the default silently re-enables workflow
  // mode on every task the operator deliberately disabled it for.
  writeUltracode('T-1', false);
  assert.equal(readUltracode('T-1', undefined, true), false);
});

test('an explicit ON survives a default of off', () => {
  writeUltracode('T-1', true);
  assert.equal(readUltracode('T-1', undefined, false), true);
});

test('a per-task choice does not leak into another task', () => {
  writeUltracode('T-1', true);
  assert.equal(readUltracode('T-2', undefined, false), false);
});
