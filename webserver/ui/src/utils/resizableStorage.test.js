import assert from 'node:assert/strict';
import test from 'node:test';

import {
  readPersistedWidth,
  writePersistedWidth,
} from './resizableStorage.js';


function fakeStorage(initial) {
  const map = new Map(initial || []);
  return {
    getItem(key) { return map.has(key) ? map.get(key) : null; },
    setItem(key, value) { map.set(key, String(value)); },
    removeItem(key) { map.delete(key); },
    _dump() { return new Map(map); },
  };
}

function brokenStorage() {
  return {
    getItem() { throw new Error('access denied'); },
    setItem() { throw new Error('quota exceeded'); },
    removeItem() { throw new Error('access denied'); },
  };
}


test('readPersistedWidth parses the stored number for a key', function () {
  const store = fakeStorage([['kato.pane.left', '320']]);

  assert.equal(readPersistedWidth('kato.pane.left', store), 320);
});

test('readPersistedWidth returns null when no value is stored', function () {
  assert.equal(readPersistedWidth('kato.pane.left', fakeStorage()), null);
});

test('readPersistedWidth returns null for an empty-string value', function () {
  // parseInt('') is NaN; the helper normalizes that to null so the
  // caller falls back to its default width instead of NaN-clamping.
  const store = fakeStorage([['kato.pane.left', '']]);

  assert.equal(readPersistedWidth('kato.pane.left', store), null);
});

test('readPersistedWidth returns null for non-numeric garbage', function () {
  const store = fakeStorage([['kato.pane.left', 'three hundred']]);

  assert.equal(readPersistedWidth('kato.pane.left', store), null);
});

test('readPersistedWidth tolerates a value with a numeric prefix (parseInt semantics)', function () {
  // parseInt('320px', 10) → 320. This is intentional: legacy or
  // hand-edited values with a unit suffix still resolve to a usable
  // number rather than getting thrown out.
  const store = fakeStorage([['kato.pane.left', '320px']]);

  assert.equal(readPersistedWidth('kato.pane.left', store), 320);
});

test('readPersistedWidth returns null when storageKey is missing', function () {
  const store = fakeStorage([['', '320']]);

  assert.equal(readPersistedWidth('', store), null);
  assert.equal(readPersistedWidth(null, store), null);
  assert.equal(readPersistedWidth(undefined, store), null);
});

test('readPersistedWidth returns null when no storage is available', function () {
  // No fake passed, no window — defaultStorage() falls through.
  // (Node has no top-level localStorage and we didn't set window.)
  assert.equal(readPersistedWidth('kato.pane.left'), null);
});

test('readPersistedWidth swallows storage errors and returns null', function () {
  // Private-browsing path: getItem can throw. The hook must still
  // mount; it just falls back to the default width.
  assert.equal(readPersistedWidth('kato.pane.left', brokenStorage()), null);
});


test('writePersistedWidth stores the width as a base-10 string', function () {
  const store = fakeStorage();

  writePersistedWidth('kato.pane.left', 360, store);

  assert.equal(store.getItem('kato.pane.left'), '360');
});

test('writePersistedWidth ignores a missing storageKey', function () {
  const store = fakeStorage();

  writePersistedWidth('', 360, store);
  writePersistedWidth(null, 360, store);

  assert.equal(store._dump().size, 0);
});

test('writePersistedWidth ignores non-finite widths', function () {
  // NaN / Infinity can leak through during pointer-move math when a
  // pointer event is mid-flight. We refuse to persist them so the
  // next mount doesn't read back garbage.
  const store = fakeStorage();

  writePersistedWidth('kato.pane.left', NaN, store);
  writePersistedWidth('kato.pane.left', Infinity, store);
  writePersistedWidth('kato.pane.left', -Infinity, store);

  assert.equal(store._dump().size, 0);
});

test('writePersistedWidth is a no-op when no storage is available', function () {
  // Must not throw — SSR or private mode where defaultStorage()
  // returns null.
  writePersistedWidth('kato.pane.left', 360);
});

test('writePersistedWidth swallows storage errors', function () {
  // Best-effort persistence: quota-exceeded must not crash a
  // mid-drag resize.
  writePersistedWidth('kato.pane.left', 360, brokenStorage());
});


test('round-trip: write then read returns the same value', function () {
  const store = fakeStorage();

  writePersistedWidth('kato.pane.left', 420, store);

  assert.equal(readPersistedWidth('kato.pane.left', store), 420);
});

test('different storage keys are isolated', function () {
  const store = fakeStorage();

  writePersistedWidth('kato.pane.left', 320, store);
  writePersistedWidth('kato.pane.right', 480, store);

  assert.equal(readPersistedWidth('kato.pane.left', store), 320);
  assert.equal(readPersistedWidth('kato.pane.right', store), 480);
});


test('readPersistedWidth uses window.localStorage when no storage is passed', function () {
  // Wire up a fake window so the defaultStorage() branch is exercised
  // — that's the production path the hook actually takes.
  const store = fakeStorage([['kato.pane.left', '512']]);
  global.window = { localStorage: store };
  try {
    assert.equal(readPersistedWidth('kato.pane.left'), 512);
  } finally {
    delete global.window;
  }
});

test('writePersistedWidth uses window.localStorage when no storage is passed', function () {
  const store = fakeStorage();
  global.window = { localStorage: store };
  try {
    writePersistedWidth('kato.pane.left', 512);
    assert.equal(store.getItem('kato.pane.left'), '512');
  } finally {
    delete global.window;
  }
});
