// Tests for the "steer while working" composer preference. Pure localStorage
// pref (no backend); we shim localStorage the same way permissionSound.test
// does and assert default / persistence / subscribe.

import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import {
  readSteerWhileWorking,
  writeSteerWhileWorking,
  subscribeSteerWhileWorking,
  _resetSteerWhileWorkingPref,
} from './composerSteerPref.js';

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
  _resetSteerWhileWorkingPref();
});

test('defaults to steer (true) when nothing is stored', () => {
  assert.equal(readSteerWhileWorking(), true);
});

test('writing false persists and reads back false', () => {
  writeSteerWhileWorking(false);
  assert.equal(readSteerWhileWorking(), false);
  // A fresh cache (as after a reload) still reads the persisted value.
  _resetSteerWhileWorkingPref();
  assert.equal(readSteerWhileWorking(), false);
});

test('writing true persists and reads back true', () => {
  writeSteerWhileWorking(false);
  writeSteerWhileWorking(true);
  assert.equal(readSteerWhileWorking(), true);
  _resetSteerWhileWorkingPref();
  assert.equal(readSteerWhileWorking(), true);
});

test('coerces truthy/falsy inputs to a real boolean', () => {
  writeSteerWhileWorking(0);
  assert.strictEqual(readSteerWhileWorking(), false);
  writeSteerWhileWorking('yes');
  assert.strictEqual(readSteerWhileWorking(), true);
});

test('subscribers are notified on change and can unsubscribe', () => {
  const seen = [];
  const unsub = subscribeSteerWhileWorking((v) => seen.push(v));
  writeSteerWhileWorking(false);
  writeSteerWhileWorking(true);
  unsub();
  writeSteerWhileWorking(false);
  assert.deepEqual(seen, [false, true]);
});

test('a corrupt stored value falls back to the default', () => {
  _store['kato.composerSteer.v1'] = '{not json';
  assert.equal(readSteerWhileWorking(), true);
});

test('an unrelated stored shape falls back to the default', () => {
  _store['kato.composerSteer.v1'] = JSON.stringify({ other: 1 });
  assert.equal(readSteerWhileWorking(), true);
});
