// A single-concern, per-task data store: it holds ONE data type keyed by
// task id, and nothing else. Lifecycle — LRU retention, eviction, and the
// active-task poll — is owned by the PARENT (./index.js via createTaskCache);
// this child only fetches / parses / dedupes / holds. It is PRIVATE: only the
// parent constructs and reads it. App code never imports a child directly — it
// goes through the parent's hooks/actions.
//
// Reactive state:  { tasks: { [taskId]: { data, status, error, lastFetched } } }
//   status ∈ 'idle' | 'loading' | 'ready' | 'error'
//
// Private bookkeeping (the in-flight Promise + the byte signature) lives in a
// closure Map, NEVER in reactive state — a load START must not churn slice
// identity, and a Promise must never leak into a selector result.

import { createStore } from 'zustand/vanilla';
import { useStore } from 'zustand';
import { useShallow } from 'zustand/react/shallow';

export function createDataStore({ fetch: fetchFn, parse, empty }) {
  const store = createStore(() => ({ tasks: {} }));
  const meta = new Map(); // taskId -> { inFlight, sig }
  // The stable slice a task-with-no-entry reads as. Frozen + shared so
  // selectors over a missing task stay referentially stable.
  const EMPTY_SLICE = Object.freeze({
    data: empty, status: 'idle', error: '', lastFetched: 0,
  });

  function metaFor(taskId) {
    let m = meta.get(taskId);
    if (!m) { m = { inFlight: null, sig: '' }; meta.set(taskId, m); }
    return m;
  }

  const sliceOf = (taskId) => store.getState().tasks[taskId] || null;

  // Immutable commit: new tasks + new tasks[id], but every OTHER task's slice
  // keeps its identity, so a task-A refresh never re-renders a task-B reader.
  function commit(taskId, patch) {
    store.setState((s) => {
      const prev = s.tasks[taskId] || EMPTY_SLICE;
      return { tasks: { ...s.tasks, [taskId]: { ...prev, ...patch } } };
    });
  }

  function errorMessage(err) {
    return String(err && err.message ? err.message : err) || 'failed to load';
  }

  // Single-flight fetch+parse+SWR. A second call while one is in flight
  // (a poll tick racing a workspace-bump revalidate) returns the SAME
  // Promise — one request, not two.
  function load(taskId) {
    if (!taskId) { return Promise.resolve(); }
    const m = metaFor(taskId);
    if (m.inFlight) { return m.inFlight; }
    const cur = sliceOf(taskId);
    // First load shows the spinner; a revalidate over existing data does NOT
    // blank it (SWR) — keep status 'ready'/'error' and the last data.
    if (!cur || cur.status === 'idle') { commit(taskId, { status: 'loading' }); }
    m.inFlight = Promise.resolve()
      .then(() => fetchFn(taskId))
      .then((payload) => {
        // Purged mid-flight (LRU eviction / forget)? Drop the result — never
        // resurrect an evicted task. `meta` no longer holds our `m` once the
        // task was purged.
        if (meta.get(taskId) !== m) { return; }
        const sig = JSON.stringify(payload);
        const patch = { status: 'ready', error: '', lastFetched: Date.now() };
        // Unchanged bytes → keep the SAME parsed `data` reference so memoized
        // consumers bail (no re-locate / no re-render).
        if (sig !== m.sig) { m.sig = sig; patch.data = parse(payload); }
        commit(taskId, patch);
      })
      .catch((err) => {
        if (meta.get(taskId) !== m) { return; }
        // Keep the last-known data on error; just surface the message.
        commit(taskId, { status: 'error', error: errorMessage(err) });
      })
      .finally(() => { m.inFlight = null; });
    return m.inFlight;
  }

  // Drop a task's data + bookkeeping entirely (the parent's LRU/forget calls
  // this — retention means we do NOT purge on React unsubscribe).
  function purge(taskId) {
    meta.delete(taskId);
    store.setState((s) => {
      if (!(taskId in s.tasks)) { return s; }
      const tasks = { ...s.tasks };
      delete tasks[taskId];
      return { tasks };
    });
  }

  const get = (taskId) => sliceOf(taskId);
  const has = (taskId) => !!sliceOf(taskId);

  // Apply a synchronous local edit to a task's data (an OPTIMISTIC mutation):
  // swap in the transformed data AND update the dedupe signature to match, so
  // a following reconcile ``load`` re-commits ONLY if the server disagrees —
  // a rejected delete restores the subtree; a confirmed one is a no-op. Only
  // sound for identity-parse stores (data === payload), i.e. comments.
  function applyLocal(taskId, transform) {
    const cur = sliceOf(taskId);
    if (!cur) { return; }
    const nextData = transform(cur.data);
    if (nextData === cur.data) { return; }
    metaFor(taskId).sig = JSON.stringify(nextData);
    commit(taskId, { data: nextData, status: 'ready' });
  }

  // Drop EVERYTHING (data + bookkeeping). For the parent's test reset only —
  // a retained singleton would otherwise bleed data/in-flight promises across
  // test cases.
  function clear() {
    meta.clear();
    store.setState({ tasks: {} });
  }

  // React read hook. `map` projects the slice to the consumer's shape;
  // useShallow keeps the result referentially stable, so an idle poll (only
  // `lastFetched` changed, `data` identity preserved) yields ZERO re-render.
  // `map` must read only rendered fields — never `lastFetched`.
  function use(taskId, map) {
    return useStore(
      store,
      useShallow((s) => map((taskId && s.tasks[taskId]) || EMPTY_SLICE)),
    );
  }

  return { store, load, purge, get, has, use, clear, applyLocal };
}
