// Orchestrator tests — NO mocks. Real createDataStore children with real
// in-memory fetchers, a real test-driven poller (a plain object we tick by
// hand), and real assertions against real state. Covers the hard cases: LRU
// retention, active-never-evicted, true-LRU victim, eviction fan-out, forget,
// single-flight coalescing, and no double-poll.

import { describe, test, expect } from 'vitest';

import { createDataStore } from './createDataStore.js';
import { createTaskCache } from './createTaskCache.js';

const flush = () => new Promise((r) => setTimeout(r, 0));

function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function makeFetcher(payload) {
  const s = { payload, calls: 0, gate: null };
  const fetch = async () => {
    s.calls += 1;
    if (s.gate) { await s.gate.promise; }
    return s.payload;
  };
  return { fetch, s };
}

// A REAL poller (real object with real start/stop/tick), driven by the test.
function makeManualPoller() {
  const pollers = [];
  const factory = (tick) => {
    const p = { tick, running: false, start() { this.running = true; }, stop() { this.running = false; } };
    pollers.push(p);
    return p;
  };
  return {
    factory, pollers,
    fireTick() { pollers.forEach((p) => { if (p.running) { p.tick(); } }); },
  };
}

function makeCache({ retain = 5 } = {}) {
  const fetchers = {};
  function mk(name) {
    const { fetch, s } = makeFetcher({ [name]: 1 });
    fetchers[name] = s;
    return createDataStore({ fetch, parse: (x) => x, empty: null });
  }
  const children = { diff: mk('diff'), tree: mk('tree') };
  const poller = makeManualPoller();
  const evicted = [];
  const cache = createTaskCache({
    children, retain, polledTypes: ['diff'], intervalMs: 5000, createPoller: poller.factory,
  });
  cache.registerOnEvict((id) => evicted.push(id));
  return { cache, children, fetchers, poller, evicted };
}


describe('createTaskCache — orchestrator', () => {
  test('setActiveTask touches the LRU and loads every type', async () => {
    const { cache, children, fetchers } = makeCache();
    cache.setActiveTask('A');
    expect(cache.getOrder()).toEqual(['A']);
    expect(cache.getActiveTaskId()).toBe('A');
    await flush();
    expect(children.diff.get('A').status).toBe('ready');
    expect(children.tree.get('A').status).toBe('ready');
    expect(fetchers.diff.calls).toBe(1);
    expect(fetchers.tree.calls).toBe(1);
  });

  test('retains only the last N; active is never evicted; true-LRU victim', async () => {
    const { cache, children, evicted } = makeCache({ retain: 3 });
    for (const id of ['A', 'B', 'C', 'D', 'E']) { cache.setActiveTask(id); }
    await flush();
    // Most-recent-first, capped at 3.
    expect(cache.getOrder()).toEqual(['E', 'D', 'C']);
    expect(cache.getActiveTaskId()).toBe('E');
    // Oldest two evicted (true LRU) and purged from every child.
    expect(evicted).toEqual(['A', 'B']);
    for (const id of ['C', 'D', 'E']) { expect(children.diff.has(id)).toBe(true); }
    for (const id of ['A', 'B']) { expect(children.diff.has(id)).toBe(false); }
    // The active task never blanks even under churn.
    expect(children.diff.get('E').status).toBe('ready');
  });

  test('switching back to a retained task serves cached data (no blank)', async () => {
    const { cache, children } = makeCache({ retain: 3 });
    cache.setActiveTask('A'); await flush();
    const aData = children.diff.get('A').data;
    cache.setActiveTask('B'); await flush();
    // A is retained (within N) — data still resident, never went to loading.
    expect(children.diff.get('A').data).toBe(aData);
    cache.setActiveTask('A'); await flush();
    expect(children.diff.get('A').status).toBe('ready');   // instant, not 'loading'
    expect(children.diff.get('A').data).toBe(aData);
  });

  test('forgetTask purges even the active task and clears the pointer', async () => {
    const { cache, children, evicted } = makeCache();
    cache.setActiveTask('A'); await flush();
    cache.forgetTask('A');
    expect(children.diff.has('A')).toBe(false);
    expect(children.tree.has('A')).toBe(false);
    expect(cache.getActiveTaskId()).toBe('');
    expect(cache.getOrder()).toEqual([]);
    expect(evicted).toContain('A');
  });

  test('the poller revalidates ONLY the active task and only polled types', async () => {
    const { cache, fetchers, poller } = makeCache();
    cache.setActiveTask('A'); await flush();
    expect(fetchers.diff.calls).toBe(1);
    expect(fetchers.tree.calls).toBe(1);
    poller.fireTick(); await flush();                      // 5s tick
    expect(fetchers.diff.calls).toBe(2);                   // diff is polled
    expect(fetchers.tree.calls).toBe(1);                   // tree is NOT polled
  });

  test('many activations create exactly ONE poller (no double-poll)', async () => {
    const { cache, fetchers, poller } = makeCache();
    cache.setActiveTask('A');
    cache.setActiveTask('B');
    cache.setActiveTask('C');
    await flush();
    expect(poller.pollers.length).toBe(1);
    const before = fetchers.diff.calls;
    poller.fireTick(); await flush();
    // One tick → the active task (C) revalidated once, not once-per-activation.
    expect(fetchers.diff.calls).toBe(before + 1);
  });

  test('revalidate coalesces with an in-flight load (single request)', async () => {
    const { cache, children, fetchers } = makeCache();
    fetchers.diff.gate = deferred();
    cache.setActiveTask('A');                              // diff load starts, gated
    cache.revalidate('A', ['diff']);                       // races it
    cache.revalidate('A', ['diff']);
    await flush();                                          // let the ONE fetch start (gated)
    expect(fetchers.diff.calls).toBe(1);                   // coalesced
    fetchers.diff.gate.resolve(); await flush();
    expect(children.diff.get('A').status).toBe('ready');
  });

  test('a background (retained, non-active) task is not polled', async () => {
    const { cache, fetchers, poller } = makeCache({ retain: 3 });
    cache.setActiveTask('A'); await flush();
    cache.setActiveTask('B'); await flush();
    const aBefore = fetchers.diff.calls;                   // includes A's initial + B's
    poller.fireTick(); await flush();                      // only B (active) refreshes
    // Exactly one new diff call (for B), none for the retained-idle A.
    expect(fetchers.diff.calls).toBe(aBefore + 1);
  });
});
