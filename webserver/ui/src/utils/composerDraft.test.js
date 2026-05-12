import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DRAFT_STORAGE_PREFIX,
  clearDraft,
  draftStorageKey,
  readDraft,
  writeDraft,
} from './composerDraft.js';


// Map-backed fake storage. localStorage's surface is ``getItem`` /
// ``setItem`` / ``removeItem`` so a thin shim is enough to exercise the
// real code path without jsdom.
function fakeStorage(initial) {
  const map = new Map(initial || []);
  return {
    getItem(key) { return map.has(key) ? map.get(key) : null; },
    setItem(key, value) { map.set(key, String(value)); },
    removeItem(key) { map.delete(key); },
    _dump() { return new Map(map); },
  };
}

// Throws-on-every-call storage — simulates private browsing where the
// browser denies localStorage access. The composer must NOT crash.
function brokenStorage() {
  return {
    getItem() { throw new Error('access denied'); },
    setItem() { throw new Error('quota exceeded'); },
    removeItem() { throw new Error('access denied'); },
  };
}


test('draftStorageKey prefixes the task id so per-task drafts do not collide', function () {
  assert.equal(draftStorageKey('TASK-42'), `${DRAFT_STORAGE_PREFIX}TASK-42`);
});

test('draftStorageKey returns empty string for missing task id', function () {
  assert.equal(draftStorageKey(''), '');
  assert.equal(draftStorageKey(null), '');
  assert.equal(draftStorageKey(undefined), '');
});


test('readDraft returns the saved draft for a task', function () {
  const store = fakeStorage([
    [`${DRAFT_STORAGE_PREFIX}T1`, 'half-written prompt'],
  ]);

  assert.equal(readDraft('T1', store), 'half-written prompt');
});

test('readDraft returns empty string when no draft has been written', function () {
  const store = fakeStorage();

  assert.equal(readDraft('T1', store), '');
});

test('readDraft returns empty string when the task id is missing', function () {
  const store = fakeStorage([
    [`${DRAFT_STORAGE_PREFIX}`, 'orphan'],
  ]);

  assert.equal(readDraft('', store), '');
  assert.equal(readDraft(null, store), '');
});

test('readDraft swallows storage errors and returns empty', function () {
  // Bug 3 guard: in private browsing localStorage.getItem can throw —
  // the composer must still render rather than crash the whole tab.
  assert.equal(readDraft('T1', brokenStorage()), '');
});


test('writeDraft stores the value under the per-task key', function () {
  const store = fakeStorage();

  writeDraft('T1', 'pending question about migration', store);

  assert.equal(
    store.getItem(`${DRAFT_STORAGE_PREFIX}T1`),
    'pending question about migration',
  );
});

test('writeDraft removes the entry when value is empty', function () {
  // Empty drafts should not linger — submit() clears, and a stale
  // entry would re-hydrate next tab return with text the user already
  // sent.
  const store = fakeStorage([
    [`${DRAFT_STORAGE_PREFIX}T1`, 'old text'],
  ]);

  writeDraft('T1', '', store);

  assert.equal(store.getItem(`${DRAFT_STORAGE_PREFIX}T1`), null);
});

test('writeDraft is a no-op when the task id is missing', function () {
  const store = fakeStorage();

  writeDraft('', 'should be ignored', store);
  writeDraft(null, 'should be ignored', store);

  assert.equal(store._dump().size, 0);
});

test('writeDraft swallows storage errors', function () {
  // Best-effort persistence: a quota-exceeded write must not crash
  // the composer mid-keystroke.
  writeDraft('T1', 'x'.repeat(10), brokenStorage());
});


test('clearDraft removes the saved draft for a task', function () {
  const store = fakeStorage([
    [`${DRAFT_STORAGE_PREFIX}T1`, 'about to send'],
  ]);

  clearDraft('T1', store);

  assert.equal(store.getItem(`${DRAFT_STORAGE_PREFIX}T1`), null);
});


test('drafts for different tasks are isolated (Bug 3 root cause)', function () {
  // Bug 3 was: composer state lived in MessageForm only, so switching
  // tabs blew away the in-progress text. The fix keys each draft by
  // ``taskId`` — this test pins that contract so a regression that
  // shares one global key would fail immediately.
  const store = fakeStorage();

  writeDraft('TASK-A', 'message-for-A', store);
  writeDraft('TASK-B', 'message-for-B', store);

  assert.equal(readDraft('TASK-A', store), 'message-for-A');
  assert.equal(readDraft('TASK-B', store), 'message-for-B');

  clearDraft('TASK-A', store);

  assert.equal(readDraft('TASK-A', store), '');
  assert.equal(readDraft('TASK-B', store), 'message-for-B');
});


test('readDraft / writeDraft fall back to window.localStorage when no storage is passed', function () {
  // Exercises the defaultStorage() branch — the production path the
  // composer actually takes (no storage arg, runs in a real browser).
  const store = fakeStorage();
  global.window = { localStorage: store };
  try {
    writeDraft('T1', 'production-path');
    assert.equal(readDraft('T1'), 'production-path');
    assert.equal(store.getItem(`${DRAFT_STORAGE_PREFIX}T1`), 'production-path');
  } finally {
    delete global.window;
  }
});

test('readDraft / writeDraft return / no-op gracefully when window is absent', function () {
  // SSR / Node-no-window path: must not throw, must return empty.
  assert.equal(readDraft('T1'), '');
  writeDraft('T1', 'should-vanish');
});


test('round-trip survives a simulated tab unmount/remount', function () {
  // Mirrors what happens when the operator types in tab A, switches
  // to tab B, then comes back: MessageForm unmounts, remounts, and on
  // mount calls readDraft(taskId).
  const store = fakeStorage();

  // Tab A: operator types.
  writeDraft('TASK-A', 'half a sentence about', store);

  // ...switches tabs (unmount). Tab B mounts and reads its own
  // (empty) draft.
  assert.equal(readDraft('TASK-B', store), '');

  // ...switches back. Tab A remounts and re-hydrates.
  assert.equal(readDraft('TASK-A', store), 'half a sentence about');
});
