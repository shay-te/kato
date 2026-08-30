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
  // Cadence for a task whose agent is asleep. The poll cannot simply STOP
  // when idle: diff/tree/comments also change from outside the agent — the
  // operator's own terminal in the workspace, and server-side comment
  // ingestion — and neither routes through ``bumpWorkspaceVersion``, the
  // event-driven revalidate that covers agent edits. So it backs off instead.
  idleIntervalMs = 30000,
  // ``(taskId) => boolean`` — is the agent doing anything that could change
  // the workspace? Injected like ``children`` and ``createPoller`` so the
  // orchestrator stays testable without the status store. Defaults to "always
  // active", i.e. the original fixed cadence.
  isTaskLive = () => true,
  createPoller = defaultCreatePoller,
  // Injectable clock. The idle gate measures WALL time, not ticks — see
  // ensurePoller — so the tests need to drive it.
  now = () => Date.now(),
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

  // Ticks stay on ``intervalMs`` and the IDLE ones are dropped, rather than
  // restarting the poller at a second interval. A tick that decides to do
  // nothing costs a closure call; a stop/start dance on every liveness flip
  // would add a state machine to the one place that must not get complicated,
  // and the poller's own visibility skip already assumes a fixed cadence.
  function ensurePoller() {
    if (poller) { return; }
    // WALL time since the last refresh, not a count of ticks.
    //
    // Counting ticks looks equivalent and is not: ``createPoller`` SKIPS the
    // tick entirely while the tab is hidden, so a tick counter stops
    // advancing exactly when the most time is passing. After ten minutes in
    // the background the counter still read zero, and the operator came back
    // to ten-minute-old file and diff content — then waited a further 30
    // seconds for it. Before the backoff that wait was 5 seconds, so the
    // counter turned a saving into a regression on the one path where stale
    // means "you are reading the wrong code".
    //
    // Against the clock, a hidden stretch counts in full, so the catch-up
    // tick ``createPoller`` fires on return refreshes immediately.
    let lastPolledAt = 0;
    poller = createPoller(() => {
      const id = parent.getState().activeTaskId;
      if (!id) { return; }
      const at = now();
      if (isTaskLive(id) || at - lastPolledAt >= idleIntervalMs) {
        lastPolledAt = at;
        revalidate(id, polled);
      }
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
