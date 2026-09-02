// Unit tests for the file-tab-strip helper. Pure functions, no React,
// no DOM — matches the pinnedTabs.js test convention (runs on
// node:test, see package.json's test:node script).

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  closeTab,
  findTab,
  patchTab,
  sortPinnedFirst,
  tabKeyFor,
  togglePin,
  upsertTab,
} from './fileTabs.js';


function info(overrides = {}) {
  return {
    absolutePath: '/wks/T1/client/src/auth.py',
    relativePath: 'src/auth.py',
    repoId: 'client',
    ...overrides,
  };
}


test('tabKeyFor keys on the absolute path (the identity every opener shares)', () => {
  assert.equal(tabKeyFor(info()), '/wks/T1/client/src/auth.py');
});

test('tabKeyFor ignores repoId/relativePath differences for the same physical file', () => {
  // The file tree opens with {absolutePath, relativePath, repoId}; the chat
  // event-log "reveal" opens the SAME file with only {absolutePath}. Both must
  // yield the SAME key so they focus one tab instead of duplicating it.
  const fromTree = tabKeyFor(info());
  const fromEventLog = tabKeyFor({ absolutePath: '/wks/T1/client/src/auth.py' });
  assert.equal(fromTree, fromEventLog);
});

test('tabKeyFor trims a trailing separator so …/x and …/x/ do not split', () => {
  assert.equal(tabKeyFor({ absolutePath: '/wks/T1/client/x.py/' }), '/wks/T1/client/x.py');
});

test('tabKeyFor falls back to repoId::relativePath only when no absolute path', () => {
  assert.equal(tabKeyFor({ relativePath: 'README.md' }), '::README.md');
});

test('upsertTab appends the first tab and makes it active', () => {
  const { tabs, activeKey } = upsertTab([], null, info({ openRequestId: 1 }), 'T1');
  assert.equal(tabs.length, 1);
  assert.equal(tabs[0].key, '/wks/T1/client/src/auth.py');
  assert.equal(tabs[0].view, 'file');
  assert.equal(tabs[0].taskId, 'T1');
  assert.equal(activeKey, '/wks/T1/client/src/auth.py');
});

test('upsertTab: tree-open then event-log reveal of the same file → ONE tab, not two', () => {
  // #10 regression: the reveal button opens with only {absolutePath}. It used
  // to key differently (::absolutePath) than the tree ({repoId::relativePath})
  // and append a SECOND, degraded tab for one physical file.
  const tree = upsertTab([], null, info({ openRequestId: 1 }), 'T1');
  const revealed = upsertTab(
    tree.tabs, tree.activeKey,
    { absolutePath: '/wks/T1/client/src/auth.py', openRequestId: 2 },
    'T1',
  );
  assert.equal(revealed.tabs.length, 1);
  // ...and the reveal (no repoId/relativePath) must NOT degrade the good tab.
  assert.equal(revealed.tabs[0].repoId, 'client');
  assert.equal(revealed.tabs[0].relativePath, 'src/auth.py');
});

test('upsertTab: event-log reveal first, then tree open UPGRADES the tab', () => {
  // Opened first from the chat log (absolute path only), then from the tree
  // with the full shape — the single tab gains repoId/relativePath.
  const revealed = upsertTab(
    [], null,
    { absolutePath: '/wks/T1/client/src/auth.py', openRequestId: 1 },
    'T1',
  );
  assert.equal(revealed.tabs[0].repoId, '');
  const tree = upsertTab(revealed.tabs, revealed.activeKey, info({ openRequestId: 2 }), 'T1');
  assert.equal(tree.tabs.length, 1);
  assert.equal(tree.tabs[0].repoId, 'client');
  assert.equal(tree.tabs[0].relativePath, 'src/auth.py');
});

test('upsertTab clears a stale restoreViewState on an explicit open (one-shot)', () => {
  // #9: task-switch restore stamps restoreViewState:true on every tab; an
  // explicit open/focus is never a restore, so the merge must clear it or it
  // lingers and suppresses DiffPane's scroll-to-comment.
  const restored = [{ ...info(), key: tabKeyFor(info()), restoreViewState: true }];
  const reopened = upsertTab(restored, restored[0].key, info({ openRequestId: 9 }), 'T1');
  assert.equal(reopened.tabs[0].restoreViewState, false);
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

// --- ordering: pinned-first + drag reorder --------------------------------

const _t = (key, pinned = false) => ({ key, pinned, relativePath: key });

test('sortPinnedFirst moves pinned tabs to the front', function () {
  const out = sortPinnedFirst([_t('a'), _t('b', true), _t('c'), _t('d', true)]);
  assert.deepEqual(out.map((tab) => tab.key), ['b', 'd', 'a', 'c']);
});

test('sortPinnedFirst is STABLE within each group', function () {
  // Re-sorting on every render must never reshuffle tabs the operator
  // arranged by hand — that would make drag-to-reorder useless.
  const arranged = [_t('p1', true), _t('p2', true), _t('x'), _t('y'), _t('z')];
  assert.deepEqual(sortPinnedFirst(arranged), arranged);
});

test('sortPinnedFirst returns the same array when nothing is pinned', function () {
  const tabs = [_t('a'), _t('b')];
  assert.equal(sortPinnedFirst(tabs), tabs);
});

test('togglePin moves the tab into the pinned block at the front', function () {
  // A pin that left the tab where it was would be a badge, not a pin.
  const out = togglePin([_t('a'), _t('b'), _t('c')], 'b');
  assert.deepEqual(out.map((tab) => tab.key), ['b', 'a', 'c']);
  assert.equal(out[0].pinned, true);
});

test('pinning a second tab sits BESIDE the first, not before it', function () {
  const first = togglePin([_t('a'), _t('b'), _t('c')], 'b');
  const second = togglePin(first, 'c');
  assert.deepEqual(second.map((tab) => tab.key), ['b', 'c', 'a']);
});

test('unpinning drops the tab directly after the pins, not to the far right', function () {
  // Sending a just-unpinned tab to the end of a long strip effectively
  // hides the file the operator was looking at.
  const pinned = [_t('p', true), _t('q', true), _t('a'), _t('b')];
  const out = togglePin(pinned, 'q');
  assert.deepEqual(out.map((tab) => tab.key), ['p', 'q', 'a', 'b']);
  assert.equal(out[1].pinned, false);
});

test('togglePin on an unknown key is a no-op', function () {
  const tabs = [_t('a')];
  assert.equal(togglePin(tabs, 'nope'), tabs);
});

