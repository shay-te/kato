// The shared preference-store mechanism. Two modules had grown this same
// ~40 lines independently and they were not identical — one exported a reset
// for test isolation and one did not.
//
// The first three cases below are regressions found WHILE consolidating, each
// of which round-trips a preference back to its default without any error:
//
//   * persisting a bare scalar — JSON.parse('false') is not an object, so a
//     "restore defaults on a corrupt value" guard cannot tell it from garbage;
//   * a storage resolver that only checked window.localStorage, making every
//     write a silent no-op wherever the global is bare (which is exactly what
//     the modules' own tests shim);
//   * no reset, so a module-level cache leaks between test cases.

import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import { createPreferenceStore } from './createPreferenceStore.js';

let _raw = {};

beforeEach(() => {
  _raw = {};
  globalThis.localStorage = {
    getItem: (k) => (k in _raw ? _raw[k] : null),
    setItem: (k, v) => { _raw[k] = String(v); },
    removeItem: (k) => { delete _raw[k]; },
  };
});

function makeStore(defaults = { flag: true, mode: 'a' }) {
  return createPreferenceStore({
    key: 'test.pref.v1',
    defaults,
    coerce: (parsed, d) => ({
      flag: parsed.flag === undefined ? d.flag : !!parsed.flag,
      mode: parsed.mode === undefined ? d.mode : String(parsed.mode),
    }),
  });
}

// --- the three regressions -------------------------------------------------

test('a written FALSE survives a cache reset (not just a written true)', () => {
  const store = makeStore();
  store.write({ flag: false, mode: 'b' });
  store.reset();
  assert.equal(store.read().flag, false,
    'false round-tripped back to the default — the stored shape must be a record');
});

test('persists through a bare globalThis.localStorage with no window', () => {
  assert.equal(typeof globalThis.window, 'undefined');
  const store = makeStore();
  store.write({ flag: false, mode: 'b' });
  assert.ok(_raw['test.pref.v1'], 'nothing reached storage');
  store.reset();
  assert.equal(store.read().mode, 'b');
});

test('reset clears the cache so a later read re-reads storage', () => {
  const store = makeStore();
  assert.equal(store.read().mode, 'a');
  _raw['test.pref.v1'] = JSON.stringify({ flag: true, mode: 'z' });
  assert.equal(store.read().mode, 'a', 'cached value should still be served');
  store.reset();
  assert.equal(store.read().mode, 'z');
});

// --- the rest of the contract ---------------------------------------------

test('reads the defaults when nothing is stored', () => {
  assert.deepEqual(makeStore().read(), { flag: true, mode: 'a' });
});

test('coerce fills in a partially-stored record', () => {
  _raw['test.pref.v1'] = JSON.stringify({ mode: 'q' });
  assert.deepEqual(makeStore().read(), { flag: true, mode: 'q' });
});

test('a corrupt stored value falls back to the defaults', () => {
  _raw['test.pref.v1'] = 'not json at all';
  assert.deepEqual(makeStore().read(), { flag: true, mode: 'a' });
});

test('a stored null falls back to the defaults', () => {
  _raw['test.pref.v1'] = 'null';
  assert.deepEqual(makeStore().read(), { flag: true, mode: 'a' });
});

test('write returns the coerced record and notifies subscribers', () => {
  const store = makeStore();
  const seen = [];
  store.subscribe((v) => seen.push(v));
  const written = store.write({ flag: 0, mode: 7 });
  assert.deepEqual(written, { flag: false, mode: '7' });
  assert.deepEqual(seen, [{ flag: false, mode: '7' }]);
});

test('unsubscribe stops further notifications', () => {
  const store = makeStore();
  let count = 0;
  const off = store.subscribe(() => { count += 1; });
  store.write({ flag: false });
  off();
  store.write({ flag: true });
  assert.equal(count, 1);
});

test('a throwing subscriber does not block the others or the write', () => {
  const store = makeStore();
  let reached = false;
  store.subscribe(() => { throw new Error('bad listener'); });
  store.subscribe(() => { reached = true; });
  const written = store.write({ flag: false });
  assert.equal(reached, true);
  assert.equal(written.flag, false);
});

test('a failing setItem keeps the in-memory value instead of throwing', () => {
  // Private mode / over quota. Persistence is best-effort: the operator's
  // choice still applies for this session, it just won't survive a reload.
  globalThis.localStorage.setItem = () => { throw new Error('QuotaExceeded'); };
  const store = makeStore();
  assert.equal(store.write({ flag: false }).flag, false);
  assert.equal(store.read().flag, false);
});

test('write(undefined) resets to the defaults rather than crashing', () => {
  const store = makeStore();
  store.write({ flag: false, mode: 'b' });
  assert.deepEqual(store.write(undefined), { flag: true, mode: 'a' });
});
