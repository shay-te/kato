// Unit tests for the EditorPane file-content cache. Pure Map
// operations, no React, no DOM.

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  _clearFileContentCacheForTests,
  clearFileContentCacheForTask,
  fileContentCacheKey,
  readCachedFileContent,
  writeCachedFileContent,
} from './fileContentCache.js';

test.beforeEach(() => {
  _clearFileContentCacheForTests();
});

test('fileContentCacheKey uses :: so a space in a Windows path cannot collide', () => {
  assert.equal(
    fileContentCacheKey('T1', 'C:/Users/John Doe/repo/x.py'),
    'T1::C:/Users/John Doe/repo/x.py',
  );
});

test('readCachedFileContent returns null when nothing is cached', () => {
  assert.equal(readCachedFileContent('T1', '/a.py'), null);
});

test('write then read round-trips the exact stored value', () => {
  const value = { content: 'print(1)', binary: false, tooLarge: false, mtime: '123.0' };
  writeCachedFileContent('T1', '/a.py', value);
  assert.deepEqual(readCachedFileContent('T1', '/a.py'), value);
});

test('different tasks with the same path do not collide', () => {
  writeCachedFileContent('T1', '/a.py', { content: 'one' });
  writeCachedFileContent('T2', '/a.py', { content: 'two' });
  assert.equal(readCachedFileContent('T1', '/a.py').content, 'one');
  assert.equal(readCachedFileContent('T2', '/a.py').content, 'two');
});

test('re-writing an existing key updates the value in place', () => {
  writeCachedFileContent('T1', '/a.py', { content: 'old' });
  writeCachedFileContent('T1', '/a.py', { content: 'new' });
  assert.equal(readCachedFileContent('T1', '/a.py').content, 'new');
});

test('evicts the oldest entry once the cache exceeds its max size', () => {
  for (let i = 0; i < 51; i += 1) {
    writeCachedFileContent('T1', `/file-${i}.py`, { content: String(i) });
  }
  // The very first entry (file-0) should have been evicted...
  assert.equal(readCachedFileContent('T1', '/file-0.py'), null);
  // ...but the most recent one is still there.
  assert.equal(readCachedFileContent('T1', '/file-50.py').content, '50');
});

test('re-writing an entry counts as freshest for eviction purposes', () => {
  writeCachedFileContent('T1', '/a.py', { content: 'a' });
  for (let i = 0; i < 49; i += 1) {
    writeCachedFileContent('T1', `/file-${i}.py`, { content: String(i) });
  }
  // Cache is now at exactly 50 entries (a.py + file-0..48). Touch a.py
  // again so it's no longer the oldest...
  writeCachedFileContent('T1', '/a.py', { content: 'a-updated' });
  // ...then push one more entry past the cap.
  writeCachedFileContent('T1', '/file-49.py', { content: '49' });
  // file-0 (now the oldest untouched entry) got evicted, NOT a.py.
  assert.equal(readCachedFileContent('T1', '/file-0.py'), null);
  assert.equal(readCachedFileContent('T1', '/a.py').content, 'a-updated');
});

test('clearFileContentCacheForTask drops only that task\'s entries', () => {
  writeCachedFileContent('T1', '/a.py', { content: 'one' });
  writeCachedFileContent('T2', '/a.py', { content: 'two' });
  clearFileContentCacheForTask('T1');
  assert.equal(readCachedFileContent('T1', '/a.py'), null);
  assert.equal(readCachedFileContent('T2', '/a.py').content, 'two');
});

test('clearFileContentCacheForTask does not partial-match a task id prefix', () => {
  // 'T1' must not also clear 'T10' — the ':: ' delimiter in the key
  // prevents a bare startsWith('T1') false-positive.
  writeCachedFileContent('T1', '/a.py', { content: 'one' });
  writeCachedFileContent('T10', '/a.py', { content: 'ten' });
  clearFileContentCacheForTask('T1');
  assert.equal(readCachedFileContent('T10', '/a.py').content, 'ten');
});
