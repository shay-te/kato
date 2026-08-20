// Which task tab the operator was last looking at.
//
// Reopening kato — a browser refresh, or relaunching the desktop app —
// dropped it and landed on nothing, so the operator had to find their task
// in the strip again every single time. Restoring it is the difference
// between reopening a tool and re-entering it.
//
// Pure client-side (localStorage), like the other view preferences: which
// tab is in front is the operator's own view state, not something the
// backend has any business knowing or agreeing with across machines.

import { createPreferenceStore } from './createPreferenceStore.js';

const STORAGE_KEY = 'kato.lastActiveTask.v1';

const _store = createPreferenceStore({
  key: STORAGE_KEY,
  defaults: { taskId: '' },
  coerce: (parsed, defaults) => ({
    taskId: typeof parsed.taskId === 'string'
      ? parsed.taskId.trim()
      : defaults.taskId,
  }),
});

export function readLastActiveTask() {
  return _store.read().taskId;
}

export function writeLastActiveTask(taskId) {
  return _store.write({ taskId: String(taskId || '').trim() }).taskId;
}

export function clearLastActiveTask() {
  return writeLastActiveTask('');
}

// Test-only: the cache + listeners are module-level, so tests must reset
// between cases for isolation.
export function _resetLastActiveTask() {
  _store.reset();
}
