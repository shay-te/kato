import assert from 'node:assert/strict';
import test from 'node:test';

import {
  getLastGitActionAt,
  recordGitActionNow,
  formatGitActionTime,
  lastActionSuffix,
} from './lastGitAction.js';

// A real in-memory localStorage (node has none) — exercises the REAL
// read/write/format logic, no mocking of the module under test.
class _MemStorage {
  constructor() { this._m = new Map(); }
  getItem(k) { return this._m.has(k) ? this._m.get(k) : null; }
  setItem(k, v) { this._m.set(k, String(v)); }
  clear() { this._m.clear(); }
}
globalThis.localStorage = new _MemStorage();


test('formatGitActionTime: empty for 0 / NaN, a real string otherwise', () => {
  assert.equal(formatGitActionTime(0), '');
  assert.equal(formatGitActionTime(NaN), '');
  assert.ok(formatGitActionTime(1_700_000_000_000).length > 0);
});

test('record then read the last action time, keyed per (task, action)', () => {
  localStorage.clear();
  assert.equal(getLastGitActionAt('T1', 'merge'), 0);  // never run
  recordGitActionNow('T1', 'merge', 1_700_000_000_000);
  assert.equal(getLastGitActionAt('T1', 'merge'), 1_700_000_000_000);
  // Independent per action and per task.
  assert.equal(getLastGitActionAt('T1', 'push'), 0);
  assert.equal(getLastGitActionAt('T2', 'merge'), 0);
});

test('lastActionSuffix: "Not … from here yet" before, "Last …" after', () => {
  localStorage.clear();
  assert.match(lastActionSuffix('T1', 'pull', 'pulled'), /Not pulled from here yet/);
  recordGitActionNow('T1', 'pull', 1_700_000_000_000);
  assert.match(lastActionSuffix('T1', 'pull', 'pulled'), /Last pulled: /);
});

test('blank task or action is a safe no-op', () => {
  assert.equal(getLastGitActionAt('', 'push'), 0);
  recordGitActionNow('', 'push', 123);  // must not throw
  assert.equal(getLastGitActionAt('', 'push'), 0);
});

test('corrupt storage falls back to "never" rather than throwing', () => {
  localStorage.setItem('kato.lastGitAction.v1', 'not-json{');
  assert.equal(getLastGitActionAt('T1', 'merge'), 0);
});
