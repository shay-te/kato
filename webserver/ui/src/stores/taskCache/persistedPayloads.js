// Durable per-task payload cache — the reason a page reload does not re-fetch
// the world.
//
// The task cache's LRU already keeps the last N viewed tasks in memory, so
// SWITCHING tasks is instant. A reload empties it, and the Files pane then
// waits on ``/files``: four git passes PER REPO, so a 25-repo workspace sits
// empty for seconds on every single refresh. The operator: "there is no need
// to load all the files again after refresh. make sure it's immediately
// there."
//
// What is stored is the raw response TEXT — the exact ``JSON.stringify`` the
// child store already computes as its dedupe signature. Three reasons:
//   * no second serialization, and no structured-clone of a large object graph;
//   * the signature comes back for free, so when the background revalidate
//     returns identical bytes the child keeps the SAME parsed reference and
//     nothing re-renders — the restore is invisible rather than a flash;
//   * parsing stays the child's job, so this file never learns a payload shape
//     and works unchanged for any data type that opts in.
//
// IndexedDB, not localStorage: one repo's tree in a monorepo serializes to
// ~150 KB and a multi-repo task runs to megabytes. That does not fit
// localStorage's ~5 MB origin budget, and overflowing it would evict the
// composer drafts / pinned tabs / pane sizes that share it. ``idbStore.js`` is
// the same best-effort engine the composer's image attachments already use —
// this adds a caller, not a second storage layer.
//
// Best-effort throughout: every operation degrades to "no persistence" rather
// than throwing, so a browser with storage disabled behaves exactly as it does
// today.

import { idbGet, idbSet, idbDelete } from '../../utils/idbStore.js';

// A payload larger than this is not worth persisting: read, write and parse
// all scale with it, and the point of the cache is to be FASTER than
// re-fetching. Above the cap we keep no copy at all rather than a copy that
// costs more than it saves.
//
// Sized against the real case, not a guess. One repo of a large monorepo
// serializes to ~150 KB (measured on kato itself), so the 25-repo workspace
// this was built for lands around 4 MB. A cap anywhere near that would mean
// the biggest workspaces — the ONLY ones where the slow reload actually hurts
// — are exactly the ones that silently never get a cached copy. Parsing 16 MB
// of JSON is well under a second; the git walk it replaces is several.
const DEFAULT_MAX_CHARS = 16_000_000;

// How many tasks keep a durable copy. Deliberately smaller than the in-memory
// LRU: retention across a tab switch is that cache's job, and this one only
// has to cover the handful of tasks an operator actually reloads onto.
const DEFAULT_MAX_TASKS = 5;

export function createPersistedPayloads({
  name,
  maxTasks = DEFAULT_MAX_TASKS,
  maxChars = DEFAULT_MAX_CHARS,
}) {
  const indexKey = `kato.taskCache.${name}.index`;
  const payloadKey = (taskId) => `kato.taskCache.${name}.${taskId}`;

  // Writes run one at a time. Two overlapping writes would each read the index,
  // each append their own id, and the later one would clobber the earlier's
  // entry — leaking a payload no index row points at, forever. Chaining also
  // coalesces a burst (the agent creating several files) into ordered work
  // instead of parallel transactions racing on the same key.
  let queue = Promise.resolve();
  function enqueue(op) {
    // Each link swallows its OWN failure, which buys two things: one bad write
    // cannot wedge every later one, and the promise handed back never rejects.
    // Callers treat persistence as fire-and-forget, so a rejection here would
    // land as an unhandled rejection in a session that is otherwise fine.
    queue = queue.then(op).catch(() => undefined);
    return queue;
  }

  async function readIndex() {
    const ids = await idbGet(indexKey);
    return Array.isArray(ids) ? ids.filter((id) => typeof id === 'string' && id) : [];
  }

  // The last payload stored for ``taskId``, or ``undefined`` when there is
  // none / storage is unavailable. Reads are NOT queued behind writes — a
  // restore must not wait on unrelated persistence work.
  function read(taskId) {
    if (!taskId) { return Promise.resolve(undefined); }
    return Promise.resolve(idbGet(payloadKey(taskId)))
      .then((text) => (typeof text === 'string' ? text : undefined))
      .catch(() => undefined);
  }

  // Record ``text`` as the task's latest payload and prune beyond ``maxTasks``,
  // most-recently-written first.
  function write(taskId, text) {
    if (!taskId || typeof text !== 'string') { return Promise.resolve(); }
    // Over the cap: drop any older copy too, so a task that grew past it is
    // never restored from a stale small snapshot.
    if (text.length > maxChars) { return forget(taskId); }
    return enqueue(async () => {
      await idbSet(payloadKey(taskId), text);
      const next = [taskId, ...(await readIndex()).filter((id) => id !== taskId)];
      const evicted = next.splice(maxTasks);
      await idbSet(indexKey, next);
      await Promise.all(evicted.map((id) => idbDelete(payloadKey(id))));
    });
  }

  // Drop a task's durable copy. Called when the operator FORGETS a task — not
  // on LRU eviction, which is a memory decision and must leave the reload
  // path intact.
  function forget(taskId) {
    if (!taskId) { return Promise.resolve(); }
    return enqueue(async () => {
      await idbDelete(payloadKey(taskId));
      const index = await readIndex();
      if (index.includes(taskId)) {
        await idbSet(indexKey, index.filter((id) => id !== taskId));
      }
    });
  }

  return { read, write, forget };
}
