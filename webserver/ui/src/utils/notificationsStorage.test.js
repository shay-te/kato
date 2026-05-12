import assert from 'node:assert/strict';
import test from 'node:test';

import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import {
  DEFAULT_KIND_PREFS,
  ENABLED_STORAGE_KEY,
  KIND_STORAGE_KEY,
  defaultKindPrefs,
  readEnabled,
  readKindPrefs,
  writeEnabled,
  writeKindPrefs,
} from './notificationsStorage.js';


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


// ---------------------------------------------------------------------------
// defaults
// ---------------------------------------------------------------------------

test('DEFAULT_KIND_PREFS covers every NOTIFICATION_KIND value', function () {
  // The merge logic in readKindPrefs walks ALL_KINDS — if a new
  // NOTIFICATION_KIND is added but DEFAULT_KIND_PREFS isn't updated,
  // the read returns ``undefined`` for that kind. This test pins
  // that contract.
  for (const kind of Object.values(NOTIFICATION_KIND)) {
    assert.equal(typeof DEFAULT_KIND_PREFS[kind], 'boolean',
      `DEFAULT_KIND_PREFS missing kind: ${kind}`);
  }
});

test('defaultKindPrefs returns a fresh copy each call', function () {
  const a = defaultKindPrefs();
  const b = defaultKindPrefs();

  // Same shape...
  assert.deepEqual(a, b);
  // ...but distinct objects so callers can mutate without poisoning
  // the shared default.
  a[NOTIFICATION_KIND.STARTED] = !a[NOTIFICATION_KIND.STARTED];
  assert.notEqual(a[NOTIFICATION_KIND.STARTED], b[NOTIFICATION_KIND.STARTED]);
});


// ---------------------------------------------------------------------------
// readEnabled / writeEnabled
// ---------------------------------------------------------------------------

test('readEnabled returns true only when the stored value is exactly "on"', function () {
  assert.equal(readEnabled(fakeStorage([[ENABLED_STORAGE_KEY, 'on']])), true);
  assert.equal(readEnabled(fakeStorage([[ENABLED_STORAGE_KEY, 'off']])), false);
  assert.equal(readEnabled(fakeStorage([[ENABLED_STORAGE_KEY, 'ON']])), false);
  assert.equal(readEnabled(fakeStorage([[ENABLED_STORAGE_KEY, '']])), false);
  assert.equal(readEnabled(fakeStorage()), false);
});

test('readEnabled returns false when no storage is available', function () {
  assert.equal(readEnabled(), false);
});

test('readEnabled swallows storage errors and returns false', function () {
  // Permission-denied (private-mode) must not crash the bell icon.
  assert.equal(readEnabled(brokenStorage()), false);
});

test('writeEnabled stores "on" / "off" string', function () {
  const store = fakeStorage();

  writeEnabled(true, store);
  assert.equal(store.getItem(ENABLED_STORAGE_KEY), 'on');

  writeEnabled(false, store);
  assert.equal(store.getItem(ENABLED_STORAGE_KEY), 'off');
});

test('writeEnabled coerces truthy / falsy to on / off', function () {
  const store = fakeStorage();

  writeEnabled('yes', store);
  assert.equal(store.getItem(ENABLED_STORAGE_KEY), 'on');

  writeEnabled(0, store);
  assert.equal(store.getItem(ENABLED_STORAGE_KEY), 'off');

  writeEnabled(null, store);
  assert.equal(store.getItem(ENABLED_STORAGE_KEY), 'off');
});

test('writeEnabled is a no-op when no storage is available', function () {
  writeEnabled(true);
});

test('writeEnabled swallows storage errors', function () {
  writeEnabled(true, brokenStorage());
});


// ---------------------------------------------------------------------------
// readKindPrefs / writeKindPrefs
// ---------------------------------------------------------------------------

test('readKindPrefs returns defaults when nothing is stored', function () {
  assert.deepEqual(readKindPrefs(fakeStorage()), defaultKindPrefs());
});

test('readKindPrefs returns defaults when storage is unavailable', function () {
  assert.deepEqual(readKindPrefs(), defaultKindPrefs());
});

test('readKindPrefs hydrates from JSON', function () {
  const stored = {
    [NOTIFICATION_KIND.STARTED]: false,
    [NOTIFICATION_KIND.REPLY]: true,
  };
  const store = fakeStorage([[KIND_STORAGE_KEY, JSON.stringify(stored)]]);

  const prefs = readKindPrefs(store);

  // Stored values win where set...
  assert.equal(prefs[NOTIFICATION_KIND.STARTED], false);
  assert.equal(prefs[NOTIFICATION_KIND.REPLY], true);
  // ...defaults fill in where not.
  assert.equal(prefs[NOTIFICATION_KIND.ERROR], DEFAULT_KIND_PREFS[NOTIFICATION_KIND.ERROR]);
  assert.equal(prefs[NOTIFICATION_KIND.COMPLETED],
    DEFAULT_KIND_PREFS[NOTIFICATION_KIND.COMPLETED]);
});

test('readKindPrefs treats only the literal value false as a disable', function () {
  // Defensive coercion: any defined value that isn't strictly
  // ``false`` resolves to true. This matches the production code
  // path so a user who stored truthy garbage doesn't get silenced.
  const stored = {
    [NOTIFICATION_KIND.ERROR]: 'something-truthy',
    [NOTIFICATION_KIND.REPLY]: false,
    [NOTIFICATION_KIND.COMPLETED]: 0,
  };
  const store = fakeStorage([[KIND_STORAGE_KEY, JSON.stringify(stored)]]);

  const prefs = readKindPrefs(store);

  assert.equal(prefs[NOTIFICATION_KIND.ERROR], true);
  assert.equal(prefs[NOTIFICATION_KIND.REPLY], false);
  // 0 is JSON-falsey but !== false, so the helper keeps it true.
  // This is intentional — only the literal ``false`` is a silence.
  assert.equal(prefs[NOTIFICATION_KIND.COMPLETED], true);
});

test('readKindPrefs returns defaults for malformed JSON', function () {
  const store = fakeStorage([[KIND_STORAGE_KEY, '{not-json']]);

  assert.deepEqual(readKindPrefs(store), defaultKindPrefs());
});

test('readKindPrefs returns defaults for JSON that is not an object', function () {
  for (const garbage of ['null', '42', '"hello"', '[1,2,3]']) {
    const store = fakeStorage([[KIND_STORAGE_KEY, garbage]]);
    assert.deepEqual(readKindPrefs(store), defaultKindPrefs(),
      `expected defaults for stored value: ${garbage}`);
  }
});

test('readKindPrefs returns defaults for an empty-string value', function () {
  const store = fakeStorage([[KIND_STORAGE_KEY, '']]);

  assert.deepEqual(readKindPrefs(store), defaultKindPrefs());
});

test('readKindPrefs swallows getItem errors and returns defaults', function () {
  assert.deepEqual(readKindPrefs(brokenStorage()), defaultKindPrefs());
});

test('readKindPrefs ignores unknown kinds in stored JSON', function () {
  // A future kind that the user opted out of in a newer build, then
  // rolled back to an older build that doesn't know about it. We
  // simply ignore the unknown key — no crash, no warning.
  const stored = {
    [NOTIFICATION_KIND.ERROR]: false,
    'mystery_kind_from_the_future': false,
  };
  const store = fakeStorage([[KIND_STORAGE_KEY, JSON.stringify(stored)]]);

  const prefs = readKindPrefs(store);

  assert.equal(prefs[NOTIFICATION_KIND.ERROR], false);
  assert.equal(prefs.mystery_kind_from_the_future, undefined);
});

test('writeKindPrefs serializes prefs as JSON', function () {
  const store = fakeStorage();
  const prefs = { ...defaultKindPrefs(), [NOTIFICATION_KIND.REPLY]: true };

  writeKindPrefs(prefs, store);

  assert.deepEqual(JSON.parse(store.getItem(KIND_STORAGE_KEY)), prefs);
});

test('writeKindPrefs is a no-op when no storage is available', function () {
  writeKindPrefs({ a: 1 });
});

test('writeKindPrefs swallows storage errors', function () {
  writeKindPrefs({ a: 1 }, brokenStorage());
});


// ---------------------------------------------------------------------------
// round-trip / window-default
// ---------------------------------------------------------------------------

test('writeKindPrefs then readKindPrefs round-trips the chosen overrides', function () {
  const store = fakeStorage();
  const next = { ...defaultKindPrefs(), [NOTIFICATION_KIND.STARTED]: false };

  writeKindPrefs(next, store);

  const reloaded = readKindPrefs(store);
  assert.equal(reloaded[NOTIFICATION_KIND.STARTED], false);
});

test('readEnabled / readKindPrefs fall back to window.localStorage when no storage is passed', function () {
  // Exercises the defaultStorage() branch — the production path the
  // hook actually takes when it calls readEnabled() with no argument.
  const store = fakeStorage([
    [ENABLED_STORAGE_KEY, 'on'],
    [KIND_STORAGE_KEY, JSON.stringify({ [NOTIFICATION_KIND.REPLY]: true })],
  ]);
  global.window = { localStorage: store };
  try {
    assert.equal(readEnabled(), true);
    assert.equal(readKindPrefs()[NOTIFICATION_KIND.REPLY], true);
  } finally {
    delete global.window;
  }
});

test('writeEnabled / writeKindPrefs target window.localStorage when no storage is passed', function () {
  const store = fakeStorage();
  global.window = { localStorage: store };
  try {
    writeEnabled(true);
    writeKindPrefs({ a: 1 });
    assert.equal(store.getItem(ENABLED_STORAGE_KEY), 'on');
    assert.deepEqual(JSON.parse(store.getItem(KIND_STORAGE_KEY)), { a: 1 });
  } finally {
    delete global.window;
  }
});
