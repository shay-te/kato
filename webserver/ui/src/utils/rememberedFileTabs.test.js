// The tabs each task has open survive a reload — "I open a file, reload the
// page, the file is not opened; I need to look for it again".
//
// A reload is modelled as ``_resetRememberedFileTabs()`` (the page's memory
// is gone) with the storage kept.

import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import {
  forgetFileTabs,
  rememberFileTabs,
  rememberedFileTabTaskIds,
  rememberedFileTabs,
  _resetRememberedFileTabs,
} from './rememberedFileTabs.js';

const STORAGE_KEY = 'kato.openFileTabs.v1';
let _storage = {};
let _writes = 0;

beforeEach(() => {
  _storage = {};
  _writes = 0;
  globalThis.localStorage = {
    getItem: (key) => (key in _storage ? _storage[key] : null),
    setItem: (key, value) => { _writes += 1; _storage[key] = String(value); },
    removeItem: (key) => { delete _storage[key]; },
  };
  _resetRememberedFileTabs();
});

function reload() {
  _resetRememberedFileTabs();
}

const appPy = {
  key: '/w/T1/api/src/app.py', taskId: 'T1', absolutePath: '/w/T1/api/src/app.py',
  relativePath: 'src/app.py', repoId: 'api', view: 'diff', kind: 'modify', pinned: true,
  editorViewState: { scrollTop: 420 }, openRequestId: 7, focusComment: true, line: 12,
};
const readme = {
  key: '/w/T1/api/README.md', taskId: 'T1', absolutePath: '/w/T1/api/README.md',
  relativePath: 'README.md', repoId: 'api', view: 'file', mdView: 'preview', after: 'loop:T9',
};

test('a reload reopens the task\'s tabs and the one on show', () => {
  rememberFileTabs('T1', [appPy, readme], readme.key);
  reload();
  const restored = rememberedFileTabs('T1');
  assert.deepEqual(restored.tabs.map((tab) => tab.key), [appPy.key, readme.key]);
  assert.equal(restored.activeKey, readme.key);
});

test('storage keeps what reopens a tab — never the scroll, cursor or a one-off jump', () => {
  rememberFileTabs('T1', [appPy, readme], appPy.key);
  reload();
  const [first, second] = rememberedFileTabs('T1').tabs;
  assert.deepEqual(first, {
    key: appPy.key, taskId: 'T1', absolutePath: appPy.absolutePath, relativePath: 'src/app.py',
    repoId: 'api', view: 'diff', kind: 'modify', pinned: true,
  });
  assert.equal(second.mdView, 'preview');
  assert.equal(second.after, 'loop:T9');
});

test('within the session a task switch gets its tabs back whole, scroll included', () => {
  rememberFileTabs('T1', [appPy], appPy.key);
  assert.equal(rememberedFileTabs('T1').tabs[0].editorViewState.scrollTop, 420);
});

test('a scroll or cursor tick does not write to storage again', () => {
  rememberFileTabs('T1', [appPy], appPy.key);
  const after = _writes;
  for (const scrollTop of [10, 20, 30]) {
    rememberFileTabs('T1', [{ ...appPy, editorViewState: { scrollTop } }], appPy.key);
  }
  assert.equal(_writes, after);
  rememberFileTabs('T1', [appPy, readme], appPy.key);
  assert.equal(_writes, after + 1);
});

test('each task keeps its own tabs', () => {
  rememberFileTabs('T1', [appPy], appPy.key);
  rememberFileTabs('T2', [{ ...readme, taskId: 'T2', key: '/w/T2/README.md' }], '/w/T2/README.md');
  reload();
  assert.equal(rememberedFileTabs('T1').activeKey, appPy.key);
  assert.equal(rememberedFileTabs('T2').activeKey, '/w/T2/README.md');
  assert.deepEqual(rememberedFileTabTaskIds().sort(), ['T1', 'T2']);
});

test('closing every tab keeps nothing for the task', () => {
  rememberFileTabs('T1', [appPy], appPy.key);
  rememberFileTabs('T1', [], null);
  assert.equal(rememberedFileTabs('T1'), null);
  reload();
  assert.equal(rememberedFileTabs('T1'), null);
  assert.deepEqual(rememberedFileTabTaskIds(), []);
});

test('a task that is gone is forgotten, in memory and in storage', () => {
  rememberFileTabs('T1', [appPy], appPy.key);
  rememberFileTabs('T2', [readme], readme.key);
  forgetFileTabs(['T1']);
  assert.equal(rememberedFileTabs('T1'), null);
  reload();
  assert.equal(rememberedFileTabs('T1'), null);
  assert.ok(rememberedFileTabs('T2'));
});

test('an active key that names no remembered tab reopens none on show', () => {
  rememberFileTabs('T1', [appPy], '/nowhere');
  reload();
  assert.equal(rememberedFileTabs('T1').activeKey, null);
});

test('damaged or foreign storage reads as nothing remembered', () => {
  for (const raw of ['not json', '[]', JSON.stringify({ byTask: [] }),
    JSON.stringify({ byTask: { T1: { tabs: [{ nokey: 1 }, null, 'x'] }, '': { tabs: [appPy] } } })]) {
    _storage[STORAGE_KEY] = raw;
    reload();
    assert.equal(rememberedFileTabs('T1'), null, raw);
  }
});

test('no task id remembers nothing', () => {
  rememberFileTabs('', [appPy], appPy.key);
  assert.equal(rememberedFileTabs(''), null);
  assert.equal(_storage[STORAGE_KEY], undefined);
});

test('a restored list is a copy: changing it does not change what is kept', () => {
  rememberFileTabs('T1', [appPy], appPy.key);
  reload();
  rememberedFileTabs('T1').tabs[0].view = 'file';
  assert.equal(rememberedFileTabs('T1').tabs[0].view, 'diff');
});
