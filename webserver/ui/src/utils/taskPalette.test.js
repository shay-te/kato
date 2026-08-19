// Search/ranking model for the Ctrl+P task palette. Pure — no jsdom.

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  TASK_PALETTE_LIMIT,
  filterTaskPalette,
  nextPaletteIndex,
} from './taskPalette.js';

const S = (task_id, task_summary = '') => ({ task_id, task_summary });
const noName = () => '';
const keys = (rows) => rows.map((row) => row.taskId);

test('an empty query lists every task in strip order', function () {
  const sessions = [S('UNA-1'), S('UNA-2'), S('ABC-9')];
  assert.deepEqual(keys(filterTaskPalette(sessions, '', noName)), ['UNA-1', 'UNA-2', 'ABC-9']);
});

test('matches on the task id', function () {
  const sessions = [S('UNA-2818'), S('ABC-9')];
  assert.deepEqual(keys(filterTaskPalette(sessions, 'una', noName)), ['UNA-2818']);
});

test('matches on the ticket summary when the id is not remembered', function () {
  const sessions = [S('UNA-1', 'elastic search variables'), S('UNA-2', 'fix the login')];
  assert.deepEqual(keys(filterTaskPalette(sessions, 'elastic', noName)), ['UNA-1']);
});

test('matches on the operator RENAME shown on the tab', function () {
  // The rename is the label they are looking at — a search that ignored
  // it would fail on the exact word the operator typed.
  const sessions = [S('UNA-1', 'a summary nobody reads')];
  const rows = filterTaskPalette(sessions, 'payments', () => 'payments rewrite');
  assert.deepEqual(keys(rows), ['UNA-1']);
});

test('punctuation in an id is ignored ("una2818" finds UNA-2818)', function () {
  const rows = filterTaskPalette([S('UNA-2818')], 'una2818', noName);
  assert.deepEqual(keys(rows), ['UNA-2818']);
});

test('an initialism finds a multi-word summary', function () {
  const rows = filterTaskPalette([S('X-1', 'Elastic Search Variables')], 'esv', noName);
  assert.deepEqual(keys(rows), ['X-1']);
});

test('an id match outranks a summary match', function () {
  // Typing "una" almost always means the ticket, not a task whose
  // summary happens to contain the word.
  const sessions = [
    S('ABC-1', 'refactor the una parser'),
    S('UNA-7', 'unrelated summary'),
  ];
  assert.deepEqual(keys(filterTaskPalette(sessions, 'una', noName)), ['UNA-7', 'ABC-1']);
});

test('an exact id beats a prefix match, which beats a substring', function () {
  const sessions = [S('X-UNA-1'), S('UNA-12'), S('UNA')];
  assert.deepEqual(keys(filterTaskPalette(sessions, 'una', noName)), ['UNA', 'UNA-12', 'X-UNA-1']);
});

test('equally-ranked rows keep strip order (stable — Enter must not jitter)', function () {
  const sessions = [S('UNA-3'), S('UNA-1'), S('UNA-2')];
  assert.deepEqual(keys(filterTaskPalette(sessions, 'una', noName)), ['UNA-3', 'UNA-1', 'UNA-2']);
});

test('no match yields an empty list, not everything', function () {
  assert.deepEqual(filterTaskPalette([S('UNA-1')], 'zzzz', noName), []);
});

test('sessions without a task_id are skipped rather than rendered blank', function () {
  const rows = filterTaskPalette([{ task_summary: 'orphan' }, S('UNA-1')], '', noName);
  assert.deepEqual(keys(rows), ['UNA-1']);
});

test('the result set is capped', function () {
  const many = Array.from({ length: 200 }, (_, i) => S(`T-${i}`));
  assert.equal(filterTaskPalette(many, '', noName).length, TASK_PALETTE_LIMIT);
});

test('displayName falls back id -> summary -> rename', function () {
  const [renamed] = filterTaskPalette([S('T-1', 'summary')], '', () => 'my name');
  assert.equal(renamed.displayName, 'my name');
  const [summarised] = filterTaskPalette([S('T-2', 'summary')], '', noName);
  assert.equal(summarised.displayName, 'summary');
  const [bare] = filterTaskPalette([S('T-3')], '', noName);
  assert.equal(bare.displayName, 'T-3');
});

test('a non-array sessions value is tolerated', function () {
  assert.deepEqual(filterTaskPalette(null, 'x', noName), []);
});

test('nextPaletteIndex wraps at both ends', function () {
  // Wrapping, not clamping: the list is short and the operator is
  // holding a key — stopping dead reads as the palette having frozen.
  assert.equal(nextPaletteIndex(2, 1, 3), 0);
  assert.equal(nextPaletteIndex(0, -1, 3), 2);
  assert.equal(nextPaletteIndex(0, 1, 3), 1);
});

test('nextPaletteIndex is safe on an empty list', function () {
  assert.equal(nextPaletteIndex(0, 1, 0), 0);
  assert.equal(nextPaletteIndex(null, -1, 0), 0);
});
