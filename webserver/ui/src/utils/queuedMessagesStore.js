// Per-task outgoing-message (steer) queue.
//
// Two layers:
//   * a synchronous in-memory Map — instant restore on tab switch (the common
//     case). SessionDetail is keyed by task (App.jsx), so React drops its local
//     state when you switch tabs ("steer messages disappear when moving between
//     tasks"); the Map is what restores them on return.
//   * a durable IndexedDB backup (idbStore.js) — so the queue, INCLUDING any
//     pasted images its items carry, also survives a full page reload. Plain
//     localStorage can't hold that: base64 image data blows its ~5 MB quota,
//     which is why this used to be in-memory only and reset on reload.
//
// The IDB layer is async; SessionDetail hydrates from it after mount when the
// in-memory Map is cold (right after a reload). Writes go to both layers.

import { idbGet, idbSet, idbDelete } from './idbStore.js';

const _byTask = new Map();

const QUEUE_IDB_PREFIX = 'kato.queued-messages.';

function _idbKey(taskId) { return taskId ? `${QUEUE_IDB_PREFIX}${taskId}` : ''; }

export function readQueuedMessages(taskId) {
  if (!taskId) { return []; }
  const items = _byTask.get(taskId);
  return Array.isArray(items) ? items : [];
}

export function writeQueuedMessages(taskId, items) {
  if (!taskId) { return; }
  if (Array.isArray(items) && items.length > 0) {
    _byTask.set(taskId, items);
  } else {
    // Empty queue → drop the entry so the Map doesn't grow unbounded with
    // drained tasks.
    _byTask.delete(taskId);
  }
}

// Durable IndexedDB backup of the queue (incl. images). Best-effort and async.
// Kept separate from ``writeQueuedMessages`` so SessionDetail can gate the IDB
// write behind reload-hydration — otherwise the initial empty state on a fresh
// mount would wipe the stored queue before we get a chance to restore it.
export function persistQueuedMessages(taskId, items) {
  const key = _idbKey(taskId);
  if (!key) { return Promise.resolve(); }
  if (Array.isArray(items) && items.length > 0) {
    return idbSet(key, items);
  }
  return idbDelete(key);
}

// Restore the queue from IndexedDB after a full reload, when the in-memory Map
// is cold. A warm Map (an ordinary tab switch) wins and is returned as-is — we
// never clobber a live queue with a stale persisted one. Populates the Map so
// subsequent synchronous reads see the restored items. Returns the items.
export async function hydrateQueuedMessages(taskId) {
  if (!taskId) { return []; }
  const inMemory = _byTask.get(taskId);
  if (Array.isArray(inMemory) && inMemory.length > 0) { return inMemory; }
  const stored = await idbGet(_idbKey(taskId));
  if (Array.isArray(stored) && stored.length > 0) {
    _byTask.set(taskId, stored);
    return stored;
  }
  return [];
}

// Operator forgot/deleted a task → wipe its queue from BOTH layers (the
// in-memory Map self-cleans only on empty writes, and the durable IDB entry is
// only dropped when a queue drains normally). Forget is the complete
// operator-triggered wipe, so a forgotten task must not leave an orphaned
// base64-image-carrying entry behind in IndexedDB. Best-effort.
export function forgetQueuedMessages(taskId) {
  if (!taskId) { return Promise.resolve(); }
  _byTask.delete(taskId);
  return idbDelete(_idbKey(taskId));
}

// Test-only: the store is module-level and persists across remounts by design,
// so tests that mount SessionDetail must reset it between cases for isolation.
export function _resetQueuedMessagesStore() {
  _byTask.clear();
}
