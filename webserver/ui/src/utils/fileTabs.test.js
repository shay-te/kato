// Unit tests for the file-tab-strip helper. Pure functions, no React,
// no DOM — matches the pinnedTabs.js test convention (runs on
// node:test, see package.json's test:node script).

import assert from 'node:assert/strict';
import test from 'node:test';

import { closeTab, findTab, patchTab, tabKeyFor, upsertTab } from './fileTabs.js';


function info(overrides = {}) {
  return {
    absolutePath: '/wks/T1/client/src/auth.py',
    relativePath: 'src/auth.py',
    repoId: 'client',
    ...overrides,
  };
}


test('tabKeyFor combines repoId and relativePath', () => {
  assert.equal(tabKeyFor(info()), 'client::src/auth.py');
});

test('tabKeyFor falls back to absolutePath when relativePath is missing', () => {
  assert.equal(
    tabKeyFor({ repoId: 'client', absolutePath: '/wks/T1/client/x.py' }),
    'client::/wks/T1/client/x.py',
  );
});

test('tabKeyFor treats a blank repoId consistently (no repo attached)', () => {
  assert.equal(tabKeyFor({ relativePath: 'README.md' }), '::README.md');
});

test('upsertTab appends the first tab and makes it active', () => {
  const { tabs, activeKey } = upsertTab([], null, info({ openRequestId: 1 }), 'T1');
  assert.equal(tabs.length, 1);
  assert.equal(tabs[0].key, 'client::src/auth.py');
  assert.equal(tabs[0].view, 'file');
  assert.equal(tabs[0].taskId, 'T1');
  assert.equal(activeKey, 'client::src/auth.py');
});

test('upsertTab opening a second, different file APPENDS a new tab — never replaces', () => {
  const first = upsertTab([], null, info({ openRequestId: 1 }), 'T1');
  const second = upsertTab(
    first.tabs, first.activeKey,
    info({ relativePath: 'src/other.py', absolutePath: '/wks/T1/client/src/other.py', openRequestId: 2 }),
    'T1',
  );
  assert.equal(second.tabs.length, 2);
  assert.equal(second.tabs[0].relativePath, 'src/auth.py');
  assert.equal(second.tabs[1].relativePath, 'src/other.py');
  assert.equal(second.activeKey, second.tabs[1].key);
});

test('upsertTab inserts the new tab right after the currently active tab, not always at the end', () => {
  // Open a, b, c — then re-activate a, then open d. d should land
  // between a and b, not at the very end.
  let state = upsertTab([], null, info({ relativePath: 'a.py', absolutePath: '/a.py', openRequestId: 1 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'b.py', absolutePath: '/b.py', openRequestId: 2 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'c.py', absolutePath: '/c.py', openRequestId: 3 }), 'T1');
  // Re-focus "a" (simulates clicking its tab / re-opening it).
  const aKey = state.tabs[0].key;
  state = upsertTab(state.tabs, aKey, info({ relativePath: 'a.py', absolutePath: '/a.py', openRequestId: 4 }), 'T1');
  // Now open a brand new file "d" while "a" is active.
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'd.py', absolutePath: '/d.py', openRequestId: 5 }), 'T1');
  assert.deepEqual(state.tabs.map((t) => t.relativePath), ['a.py', 'd.py', 'b.py', 'c.py']);
  assert.equal(state.activeKey, state.tabs[1].key);
});

test('upsertTab re-opening an already-open file focuses it WITHOUT moving or duplicating it', () => {
  let state = upsertTab([], null, info({ relativePath: 'a.py', absolutePath: '/a.py', openRequestId: 1 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'b.py', absolutePath: '/b.py', openRequestId: 2 }), 'T1');
  // Re-open "a" while "b" is active.
  const beforeCount = state.tabs.length;
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'a.py', absolutePath: '/a.py', openRequestId: 3 }), 'T1');
  assert.equal(state.tabs.length, beforeCount);
  assert.deepEqual(state.tabs.map((t) => t.relativePath), ['a.py', 'b.py']);
  assert.equal(state.activeKey, state.tabs[0].key);
  assert.equal(state.tabs[0].openRequestId, 3);
});

test('upsertTab preserves remembered scroll/cursor state when re-focusing an existing tab', () => {
  let state = upsertTab([], null, info({ openRequestId: 1 }), 'T1');
  state.tabs = patchTab(state.tabs, state.activeKey, { editorViewState: { scrollTop: 500 } });
  // Open a different file, then come back to the first.
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'b.py', absolutePath: '/b.py', openRequestId: 2 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ openRequestId: 3 }), 'T1');
  const reopened = findTab(state.tabs, tabKeyFor(info()));
  assert.deepEqual(reopened.editorViewState, { scrollTop: 500 });
});

test('upsertTab toggling view (file <-> diff) updates the SAME tab in place', () => {
  let state = upsertTab([], null, info({ openRequestId: 1 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ view: 'diff', openRequestId: 2 }), 'T1');
  assert.equal(state.tabs.length, 1);
  assert.equal(state.tabs[0].view, 'diff');
});

test('closeTab removes a non-active tab and leaves the active tab unchanged', () => {
  let state = upsertTab([], null, info({ relativePath: 'a.py', absolutePath: '/a.py', openRequestId: 1 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'b.py', absolutePath: '/b.py', openRequestId: 2 }), 'T1');
  const aKey = state.tabs[0].key;
  const result = closeTab(state.tabs, state.activeKey, aKey);
  assert.equal(result.tabs.length, 1);
  assert.equal(result.tabs[0].relativePath, 'b.py');
  assert.equal(result.activeKey, state.activeKey);
});

test('closeTab on the active tab activates its left neighbor', () => {
  let state = upsertTab([], null, info({ relativePath: 'a.py', absolutePath: '/a.py', openRequestId: 1 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'b.py', absolutePath: '/b.py', openRequestId: 2 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'c.py', absolutePath: '/c.py', openRequestId: 3 }), 'T1');
  // "c" is active (last opened). Close it.
  const cKey = state.tabs[2].key;
  const result = closeTab(state.tabs, cKey, cKey);
  assert.deepEqual(result.tabs.map((t) => t.relativePath), ['a.py', 'b.py']);
  assert.equal(result.activeKey, result.tabs[1].key); // "b", c's left neighbor
});

test('closeTab on the leftmost active tab activates the new first tab', () => {
  let state = upsertTab([], null, info({ relativePath: 'a.py', absolutePath: '/a.py', openRequestId: 1 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'b.py', absolutePath: '/b.py', openRequestId: 2 }), 'T1');
  const aKey = state.tabs[0].key;
  const result = closeTab(state.tabs, aKey, aKey);
  assert.deepEqual(result.tabs.map((t) => t.relativePath), ['b.py']);
  assert.equal(result.activeKey, result.tabs[0].key);
});

test('closeTab on the last remaining tab yields an empty list and null activeKey', () => {
  const state = upsertTab([], null, info({ openRequestId: 1 }), 'T1');
  const result = closeTab(state.tabs, state.activeKey, state.tabs[0].key);
  assert.deepEqual(result.tabs, []);
  assert.equal(result.activeKey, null);
});

test('closeTab with an unknown key is a no-op', () => {
  const state = upsertTab([], null, info({ openRequestId: 1 }), 'T1');
  const result = closeTab(state.tabs, state.activeKey, 'does-not-exist');
  assert.equal(result.tabs, state.tabs);
  assert.equal(result.activeKey, state.activeKey);
});

test('patchTab merges fields onto the matching tab only', () => {
  let state = upsertTab([], null, info({ relativePath: 'a.py', absolutePath: '/a.py', openRequestId: 1 }), 'T1');
  state = upsertTab(state.tabs, state.activeKey, info({ relativePath: 'b.py', absolutePath: '/b.py', openRequestId: 2 }), 'T1');
  const aKey = state.tabs[0].key;
  const patched = patchTab(state.tabs, aKey, { diffScrollTop: 42 });
  assert.equal(patched[0].diffScrollTop, 42);
  assert.equal(patched[1].diffScrollTop, undefined);
});

test('patchTab on an unknown key returns the same array reference (safe no-op)', () => {
  const state = upsertTab([], null, info({ openRequestId: 1 }), 'T1');
  const result = patchTab(state.tabs, 'nope', { x: 1 });
  assert.equal(result, state.tabs);
});

test('findTab returns null when nothing matches', () => {
  assert.equal(findTab([], 'anything'), null);
  assert.equal(findTab(null, 'anything'), null);
});
