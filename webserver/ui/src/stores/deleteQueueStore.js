// Per-task state of a bulk delete, held app-globally.
//
// The operator's report: "when I delete tasks I don't know you are working,
// so I delete the tasks multiple times." A delete used to take 19-51 seconds
// of frozen UI with nothing on screen saying it had started. The backend is
// fast now, but a bulk run still has to say which tasks are waiting, which
// one is going, and which are finished — otherwise the same doubt returns.
//
// App-global for the same reason as ``gitActionStore``: the run OUTLIVES the
// modal that started it. Closing the dialog mid-run must not lose the
// progress, and reopening it must show where the run got to.
//
// Deliberately NOT persisted. A reload loses the browser's knowledge of the
// run, and reviving it from storage would be worse: a stale "deleting" that
// nothing will ever clear. After a reload the truth is the task list itself —
// a task that is gone is gone.

import { createPubSub } from './pubsub.js';

export const DELETE_STATE = {
  QUEUED: 'queued',
  DELETING: 'deleting',
  DONE: 'done',
  FAILED: 'failed',
};

// taskId -> { state, error }
let _byTask = {};

const _pubsub = createPubSub(() => _byTask);

function _write(next) {
  _byTask = next;
  _pubsub.emit();
}

function _set(taskId, state, error = '') {
  const key = String(taskId || '').trim();
  if (!key) { return; }
  _write({ ..._byTask, [key]: { state, error: String(error || '') } });
}

export const deleteQueue = {
  subscribe: _pubsub.subscribe,

  snapshot() { return _byTask; },

  /** Mark every id as waiting, in one emit, before the run starts. */
  enqueue(taskIds) {
    const next = { ..._byTask };
    for (const id of taskIds || []) {
      const key = String(id || '').trim();
      if (key) { next[key] = { state: DELETE_STATE.QUEUED, error: '' }; }
    }
    _write(next);
  },

  starting(taskId) { _set(taskId, DELETE_STATE.DELETING); },
  succeeded(taskId) { _set(taskId, DELETE_STATE.DONE); },
  failed(taskId, error) { _set(taskId, DELETE_STATE.FAILED, error); },

  /** True while any task is still queued or in flight. */
  isRunning() {
    return Object.values(_byTask).some(
      (entry) => entry.state === DELETE_STATE.QUEUED
        || entry.state === DELETE_STATE.DELETING,
    );
  },

  counts() {
    const out = { queued: 0, deleting: 0, done: 0, failed: 0 };
    for (const entry of Object.values(_byTask)) {
      if (entry.state in out) { out[entry.state] += 1; }
    }
    return out;
  },

  /** Drop finished entries. A FAILED one is kept — the operator has not seen it yet. */
  clearFinished() {
    const next = {};
    for (const [id, entry] of Object.entries(_byTask)) {
      if (entry.state !== DELETE_STATE.DONE) { next[id] = entry; }
    }
    _write(next);
  },

  reset() { _write({}); },
};
