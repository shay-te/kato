// A bounded, per-task map in localStorage.
//
// Two panes keep a per-task display hint this way: the repo list a task's
// Files pane last saw (``taskRepoMemory``) and which of those repos the
// operator folded away (``repoCollapseMemory``). Both need the same three
// things — read the map, write one task's entry, and drop the oldest entries
// once it outgrows a cap — and writing that twice is how two copies of a cap
// drift apart.
//
// Each caller still owns the SHAPE of its own entry; this owns only the
// keying, the stamping and the bound. Storage access goes through the shared
// helpers (``storage.js`` / ``json.js``), like every other persisted module.

import { parseJsonOr } from './json.js';
import { readStorageString, writeStorageItem } from './storage.js';

// Enough for a long working session without letting the entry grow forever.
// Oldest-WRITTEN entries are dropped first.
const MAX_TASKS = 50;

export function createTaskKeyedStore(storageKey, { maxTasks = MAX_TASKS } = {}) {
  function readAll() {
    const parsed = parseJsonOr(readStorageString(storageKey, ''), {});
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed : {};
  }

  // One task's entry, or ``undefined``. A blank id reads as nothing rather
  // than as some accidental key.
  function readEntry(taskId) {
    const key = String(taskId || '').trim();
    return key ? readAll()[key] : undefined;
  }

  // Record one task's entry, stamped so the cap can drop the oldest first. A
  // blank id writes nothing: that entry could never be read back, and it
  // would still take a slot under the cap.
  function writeEntry(taskId, entry) {
    const key = String(taskId || '').trim();
    if (!key) { return; }
    const all = readAll();
    // Re-inserted, not updated in place, so key order IS write order. Two
    // writes in the same millisecond carry the same ``at``, and the sort
    // below is stable — without this it would fall back to the original
    // order and evict the task just written.
    delete all[key];
    all[key] = { ...entry, at: Date.now() };
    const keys = Object.keys(all);
    if (keys.length > maxTasks) {
      keys
        .sort((a, b) => (all[a].at || 0) - (all[b].at || 0))
        .slice(0, keys.length - maxTasks)
        .forEach((oldKey) => { delete all[oldKey]; });
    }
    writeStorageItem(storageKey, JSON.stringify(all));
  }

  return { readEntry, writeEntry };
}
