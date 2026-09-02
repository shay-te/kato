// The shared drag-to-reorder rule for BOTH tab strips.
//
// Moved here with the implementation: the task strip needs the identical
// pinned-group behaviour, and leaving these beside the file-tab helpers would
// have implied the rule belonged to one strip.

import test from 'node:test';
import assert from 'node:assert/strict';
import { canDropOn, moveTab } from './tabOrder.js';

function _t(key, pinned = false) {
  return { key, pinned };
}

test('moveTab reorders within the unpinned group', function () {
  const out = moveTab([_t('a'), _t('b'), _t('c')], 'a', 'c');
  assert.deepEqual(out.map((tab) => tab.key), ['b', 'c', 'a']);
});

test('moveTab reorders within the pinned group', function () {
  const out = moveTab([_t('a', true), _t('b', true), _t('c')], 'b', 'a');
  assert.deepEqual(out.map((tab) => tab.key), ['b', 'a', 'c']);
});

test('moveTab REFUSES a cross-group drop instead of silently relocating', function () {
  // Snapping the tab somewhere the operator did not drop it reads as a
  // broken drag; leaving it put reads as "that is not allowed".
  const tabs = [_t('p', true), _t('a'), _t('b')];
  assert.equal(moveTab(tabs, 'a', 'p'), tabs);
  assert.equal(moveTab(tabs, 'p', 'b'), tabs);
});

test('moveTab is a no-op for unknown keys or a self-drop', function () {
  const tabs = [_t('a'), _t('b')];
  assert.equal(moveTab(tabs, 'a', 'a'), tabs);
  assert.equal(moveTab(tabs, 'nope', 'b'), tabs);
  assert.equal(moveTab(tabs, 'a', 'nope'), tabs);
  assert.equal(moveTab(tabs, '', 'b'), tabs);
});

test('moveTab keys on a caller-supplied field', () => {
  // The task strip keys on ``task_id`` and reads pinned state from a set,
  // so it must not have to reshape its data just to reorder it.
  const rows = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
  const out = moveTab(rows, 'a', 'c', {
    keyOf: (row) => row.id,
    pinnedOf: () => false,
  });
  assert.deepEqual(out.map((r) => r.id), ['b', 'c', 'a']);
});

test('moveTab refuses a cross-group drop with custom accessors too', () => {
  const rows = [{ id: 'a' }, { id: 'b' }];
  const out = moveTab(rows, 'a', 'b', {
    keyOf: (row) => row.id,
    pinnedOf: (row) => row.id === 'a',
  });
  assert.deepEqual(out.map((r) => r.id), ['a', 'b']);
});

test('canDropOn allows a same-group target', () => {
  assert.equal(canDropOn(_t('a', true), _t('b', true)), true);
  assert.equal(canDropOn(_t('a'), _t('b')), true);
});

test('canDropOn refuses across the pinned boundary', () => {
  assert.equal(canDropOn(_t('a', true), _t('b', false)), false);
  assert.equal(canDropOn(_t('a', false), _t('b', true)), false);
});

test('canDropOn refuses a tab dropped on itself, and missing tabs', () => {
  const same = _t('a');
  assert.equal(canDropOn(same, same), false);
  assert.equal(canDropOn(null, _t('b')), false);
  assert.equal(canDropOn(_t('a'), null), false);
});
