import assert from 'node:assert/strict';
import test from 'node:test';

import { filterPermissionRows } from './ClaudePermissionsHelpers.js';

const ROWS = [
  { key: 'Edit', tool: 'Edit', command: '', decision: 'allow' },
  { key: 'Bash mvn -B verify', tool: 'Bash', command: 'mvn -B verify', decision: 'allow' },
  { key: 'Bash docker run x', tool: 'Bash', command: 'docker run x', decision: 'deny' },
];

test('empty query returns all rows', () => {
  assert.equal(filterPermissionRows(ROWS, '').length, 3);
  assert.equal(filterPermissionRows(ROWS, '   ').length, 3);
});

test('matches on tool name', () => {
  const out = filterPermissionRows(ROWS, 'edit');
  assert.deepEqual(out.map((r) => r.key), ['Edit']);
});

test('matches on command substring (case-insensitive)', () => {
  const out = filterPermissionRows(ROWS, 'DOCKER');
  assert.deepEqual(out.map((r) => r.key), ['Bash docker run x']);
});

test('matches across tool + command haystack', () => {
  // "bash mvn" spans the tool name and the command.
  const out = filterPermissionRows(ROWS, 'bash mvn');
  assert.deepEqual(out.map((r) => r.key), ['Bash mvn -B verify']);
});

test('no match returns empty', () => {
  assert.deepEqual(filterPermissionRows(ROWS, 'zzz'), []);
});

test('tolerates null rows', () => {
  assert.deepEqual(filterPermissionRows(null, 'x'), []);
});
