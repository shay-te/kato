// The tabs each task has open — its files, its plan board, its task — and
// the one on show, so a reload (or relaunching the desktop app) opens them
// again. It used to drop them: "I open a file, reload the page, the file is
// not opened; I need to look for it again" (2026-11-18).
//
// ONE place for it, two layers:
//   - in memory, each tab whole — with the editor's scroll and cursor — for
//     switching between tasks without losing your place;
//   - in the browser's storage, through ``createPreferenceStore`` — the same
//     mechanism that remembers which task was selected (``lastActiveTask``) —
//     only what REOPENS a tab: which file, which view, pinned, where it sits.
//     Never the scroll and cursor, which change on every tick: storage is
//     written only when what it keeps changes.
//
// Pure client-side view state, like the selected task: the backend has no
// business knowing which files someone has open.

import { createPreferenceStore } from './createPreferenceStore.js';

const STORAGE_KEY = 'kato.openFileTabs.v1';

// What reopens a tab. Everything else on a tab is how it was being looked at.
const KEPT_FIELDS = Object.freeze([
  'key', 'taskId', 'absolutePath', 'relativePath', 'repoId', 'view', 'kind', 'pinned', 'after', 'mdView',
]);

function keptTab(tab) {
  const kept = {};
  for (const field of KEPT_FIELDS) {
    if (tab[field] !== undefined && tab[field] !== null && tab[field] !== '') { kept[field] = tab[field]; }
  }
  return kept;
}

function keptView(view) {
  const tabs = (Array.isArray(view?.tabs) ? view.tabs : [])
    .filter((tab) => tab && typeof tab === 'object' && typeof tab.key === 'string' && tab.key)
    .map(keptTab);
  if (!tabs.length) { return null; }
  const activeKey = tabs.some((tab) => tab.key === view.activeKey) ? view.activeKey : null;
  return { tabs, activeKey };
}

function keptByTask(byTask) {
  const kept = {};
  if (!byTask || typeof byTask !== 'object' || Array.isArray(byTask)) { return kept; }
  for (const [taskId, view] of Object.entries(byTask)) {
    const value = keptView(view);
    if (taskId && value) { kept[taskId] = value; }
  }
  return kept;
}

const _store = createPreferenceStore({
  key: STORAGE_KEY,
  defaults: { byTask: {} },
  coerce: (parsed) => ({ byTask: keptByTask(parsed.byTask) }),
});

// taskId -> { tabs, activeKey }, each tab whole.
let _inMemory = {};

function _id(taskId) {
  return String(taskId || '').trim();
}

function _save(byTask) {
  if (JSON.stringify(byTask) === JSON.stringify(_store.read().byTask)) { return; }
  _store.write({ byTask });
}

// What ``taskId`` had open: this session's tabs whole, else the saved ones
// (fresh copies). null when it had nothing.
export function rememberedFileTabs(taskId) {
  const id = _id(taskId);
  if (!id) { return null; }
  if (_inMemory[id]) { return _inMemory[id]; }
  const saved = _store.read().byTask[id];
  return saved ? { tabs: saved.tabs.map((tab) => ({ ...tab })), activeKey: saved.activeKey } : null;
}

// Every change to the tabs of ``taskId`` comes here. No tabs = nothing kept.
export function rememberFileTabs(taskId, tabs, activeKey) {
  const id = _id(taskId);
  if (!id) { return; }
  const list = Array.isArray(tabs) ? tabs : [];
  const byTask = { ..._store.read().byTask };
  const kept = keptView({ tabs: list, activeKey });
  if (list.length) { _inMemory[id] = { tabs: list, activeKey }; } else { delete _inMemory[id]; }
  if (kept) { byTask[id] = kept; } else { delete byTask[id]; }
  _save(byTask);
}

// The tasks are gone (deleted, or no longer in kato's list): keep nothing.
export function forgetFileTabs(taskIds) {
  const byTask = { ..._store.read().byTask };
  for (const taskId of taskIds || []) {
    const id = _id(taskId);
    delete _inMemory[id];
    delete byTask[id];
  }
  _save(byTask);
}

// Every task something is remembered for.
export function rememberedFileTabTaskIds() {
  return [...new Set([...Object.keys(_inMemory), ...Object.keys(_store.read().byTask)])];
}

// Test-only: the memory and the store's cache are module-level.
export function _resetRememberedFileTabs() {
  _inMemory = {};
  _store.reset();
}
