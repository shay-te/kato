import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_TAB_NAME_LENGTH,
  readTabNames,
  setTabName,
  tabNameFor,
  writeTabNames,
  TAB_NAMES_STORAGE_KEY,
} from './taskTabNames.js';

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
    removeItem: (k) => map.delete(k),
    _map: map,
  };
}

test('round-trips a rename through storage', () => {
  const storage = fakeStorage();
  writeTabNames({ 'UNA-1': 'Zapier fix' }, storage);
  assert.deepEqual(readTabNames(storage), { 'UNA-1': 'Zapier fix' });
});

test('falls back to the ticket summary when there is no override', () => {
  assert.equal(tabNameFor({}, 'UNA-1', 'Fix Zapier for Daniella'),
    'Fix Zapier for Daniella');
});

test('the override wins over the ticket summary', () => {
  assert.equal(tabNameFor({ 'UNA-1': 'Zapier' }, 'UNA-1', 'Fix Zapier…'), 'Zapier');
});

test('a blank override clears it rather than blanking the tab', () => {
  const next = setTabName({ 'UNA-1': 'Zapier' }, 'UNA-1', '   ');
  assert.deepEqual(next, {});
  assert.equal(tabNameFor(next, 'UNA-1', 'Fix Zapier…'), 'Fix Zapier…');
});

test('renames are capped so a paste accident cannot fill storage', () => {
  const next = setTabName({}, 'UNA-1', 'x'.repeat(500));
  assert.equal(next['UNA-1'].length, MAX_TAB_NAME_LENGTH);
});

test('setTabName never mutates the map it was given', () => {
  const before = { 'UNA-1': 'a' };
  setTabName(before, 'UNA-2', 'b');
  assert.deepEqual(before, { 'UNA-1': 'a' });
});

test('malformed storage payloads degrade to no overrides', () => {
  for (const raw of ['not json', '[]', 'null', '"str"', '5']) {
    assert.deepEqual(
      readTabNames(fakeStorage({ [TAB_NAMES_STORAGE_KEY]: raw })), {},
      raw,
    );
  }
});

test('non-string / blank entries are dropped on read', () => {
  const storage = fakeStorage({
    [TAB_NAMES_STORAGE_KEY]: JSON.stringify({ 'UNA-1': 5, 'UNA-2': '  ', 'UNA-3': 'ok' }),
  });
  assert.deepEqual(readTabNames(storage), { 'UNA-3': 'ok' });
});

test('a throwing storage never breaks the strip', () => {
  const hostile = {
    getItem() { throw new Error('denied'); },
    setItem() { throw new Error('denied'); },
  };
  assert.deepEqual(readTabNames(hostile), {});
  assert.doesNotThrow(() => writeTabNames({ 'UNA-1': 'x' }, hostile));
});
