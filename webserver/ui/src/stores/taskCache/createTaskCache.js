// The PARENT orchestrator — the single brain over the per-type child stores.
// It owns everything cross-cutting: the active-task pointer, the last-N LRU of
// viewed tasks, the one background poller, eviction (fan-out to every child),
// and the cross-type rules ("a workspace bump revalidates diff+tree+comments";
// "forget purges everything for a task"). App code never touches a child — it
// calls the actions/hooks this parent exposes.
//
// Children and the poller factory are injected so the whole orchestrator is
// testable with REAL in-memory children (no network) and a REAL, test-driven
// poller (no wall-clock) — no mocks.

import { createStore } from 'zustand/vanilla';
import { createPoller as defaultCreatePoller } from '../createPoller.js';

export function createTaskCache({
  children,
  retain = 5,
  polledTypes,
  intervalMs = 5000,
  createPoller = defaultCreatePoller,
}) {
  const childList = Object.values(children);
  const polled = polledTypes && polledTypes.length ? polledTypes : Object.keys(children);

  // Orchestration state only: the active pointer + the LRU order (most-recent
  // FIRST). Per-task DATA lives in the children, never here.
  const parent = createStore(() => ({ activeTaskId: '', order: [] }));
  const onEvictFns = new Set();
  let poller = null;

  // One eviction authority for ALL data types: drop the task from every child
  // + notify registered purgers (the satellite caches — file content, stream).
  function purgeEverywhere(taskId) {
    for (const child of childList) { child.purge(taskId); }
    for (const fn of onEvictFns) {
      try { fn(taskId); } catch (_) { /* one purger must not break others */ }
    }
  }

  // Move taskId to the front of the LRU, then evict the oldest IDLE tasks
  // beyond `retain`. The active task is `order[0]` and is never a victim.
  function touchAndEvict(taskId) {
    parent.setState((s) => ({
      order: [taskId, ...s.order.filter((t) => t !== taskId)],
    }));
    const { order, activeTaskId } = parent.getState();
    if (order.length <= retain) { return; }
    const next = [...order];
    const victims = [];
    while (next.length > retain) {
      let idx = next.length - 1;
      while (idx >= 0 && next[idx] === activeTaskId) { idx -= 1; }
      if (idx < 0) { break; } // only the active task remains — never evict it
      victims.push(next.splice(idx, 1)[0]);
    }
    if (victims.length) {
      parent.setState({ order: next });
      victims.forEach(purgeEverywhere);
    }
  }

  // Refresh named types (or all) for a task. Callers say WHAT changed, not
  // HOW to refetch. Single-flight in each child coalesces overlapping calls.
  function revalidate(taskId, types) {
    if (!taskId) { return; }
    const names = types && types.length ? types : Object.keys(children);
    for (const name of names) {
      if (children[name]) { children[name].load(taskId); }
    }
  }

  function ensurePoller() {
    if (poller) { return; }
    poller = createPoller(() => {
      const id = parent.getState().activeTaskId;
      if (id) { revalidate(id, polled); }
    }, intervalMs);
    poller.start();
  }

  // THE lifecycle entry point: call whenever the viewed task changes. Retained
  // data (if any) is already in the children, so the switch renders instantly;
  // we then revalidate all types in the background (SWR).
  function setActiveTask(taskId) {
    if (!taskId) { return; }
    touchAndEvict(taskId);
    parent.setState({ activeTaskId: taskId });
    ensurePoller();
    revalidate(taskId, Object.keys(children));
  }

  // Drop a task unconditionally (even the active one) — the operator forgot it.
  function forgetTask(taskId) {
    if (!taskId) { return; }
    parent.setState((s) => ({
      order: s.order.filter((t) => t !== taskId),
      activeTaskId: s.activeTaskId === taskId ? '' : s.activeTaskId,
    }));
    purgeEverywhere(taskId);
  }

  // Register a satellite-cache purger (file-content / stream). Governs their
  // lifetime by the SAME LRU as the children.
  function registerOnEvict(fn) {
    onEvictFns.add(fn);
    return () => onEvictFns.delete(fn);
  }

  // Full reset — for test isolation only (the singleton retains data, so a
  // stale entry/in-flight promise would otherwise bleed across test cases).
  function reset() {
    if (poller) { poller.stop(); poller = null; }
    onEvictFns.clear();
    parent.setState({ activeTaskId: '', order: [] });
    for (const child of childList) { child.clear(); }
  }

  return {
    parent,
    setActiveTask,
    forgetTask,
    revalidate,
    registerOnEvict,
    reset,
    // Introspection for tests + App (read-only).
    getActiveTaskId: () => parent.getState().activeTaskId,
    getOrder: () => parent.getState().order,
    stop: () => { if (poller) { poller.stop(); poller = null; } },
  };
}
