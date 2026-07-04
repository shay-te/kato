// Shared skeleton for a per-task, poll-backed pub/sub cache.
//
// commentStore and diffStore are the same shape: a Map(taskId -> entry)
// where each entry holds some cached server data + a snapshot, a listener
// Set, and a visibility-aware poll loop; a ref-counted subscribe starts
// the poll on the first subscriber and drops the entry on the last. This
// factory owns exactly that lifecycle so the two stores don't each carry a
// copy of it (they used to, and the copies were a dedup-gate finding).
//
// The caller supplies the data-specific parts:
//   - ``createEntryState()`` → the per-entry mutable fields, INCLUDING the
//     initial ``snapshot`` (a fresh entry is "loading").
//   - ``emptySnapshot`` → the neutral snapshot ``getSnapshot`` returns for a
//     task with no entry yet.
//   - ``load(entry)`` → fetch + reduce + ``emit`` (single-flight is the
//     caller's; it typically guards on ``entry.inFlight``).
//   - ``intervalMs`` → poll cadence.
//
// Returned: ``{ tasks, entryFor, emit, subscribe, getSnapshot, poke }`` —
// the store re-exports subscribe/getSnapshot/poke and uses tasks/entryFor/
// emit to implement its own fetch + mutations.

import { createPoller } from './createPoller.js';

export function createTaskCacheStore({
  intervalMs, emptySnapshot, createEntryState, load,
}) {
  const tasks = new Map();

  function entryFor(taskId) {
    let entry = tasks.get(taskId);
    if (!entry) {
      entry = { taskId, listeners: new Set(), poller: null, ...createEntryState() };
      entry.poller = createPoller(() => load(entry), intervalMs);
      tasks.set(taskId, entry);
    }
    return entry;
  }

  // Set the entry's snapshot and fan it out. The snapshot identity only
  // changes here, so a subscriber handed the same snapshot back (idle poll,
  // unchanged bytes) can skip work.
  function emit(entry, snapshot) {
    entry.snapshot = snapshot;
    for (const fn of entry.listeners) {
      try { fn(snapshot); } catch (_) { /* never let one subscriber break others */ }
    }
  }

  function subscribe(taskId, fn) {
    if (!taskId) { return () => {}; }
    const entry = entryFor(taskId);
    entry.listeners.add(fn);
    try { fn(entry.snapshot); } catch (_) { /* see emit */ }
    if (entry.listeners.size === 1) {
      entry.poller.start();
      load(entry);
    }
    return () => {
      entry.listeners.delete(fn);
      if (entry.listeners.size === 0) {
        entry.poller.stop();
        tasks.delete(entry.taskId);
      }
    };
  }

  function getSnapshot(taskId) {
    const entry = tasks.get(taskId);
    return entry ? entry.snapshot : emptySnapshot;
  }

  // Force an immediate reconcile (a workspaceVersion bump / post-mutation).
  // Coalesced by the caller's single-flight guard; a no-op when nobody's
  // subscribed to the task.
  function poke(taskId) {
    const entry = tasks.get(taskId);
    if (entry) { load(entry); }
  }

  return { tasks, entryFor, emit, subscribe, getSnapshot, poke };
}
