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

function makeCache({ retain = 5, ...extra } = {}) {
  // Wall clock the test drives — the idle gate measures elapsed TIME, not
  // ticks, so that hidden stretches (when createPoller skips the tick
  // entirely) still count.
  const clock = { ms: 0, advance(by) { this.ms += by; } };
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
    now: () => clock.ms,
    ...extra,
  });
  cache.registerOnEvict((id) => evicted.push(id));
  return { cache, children, fetchers, poller, evicted, clock };
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


// Idle backoff.
//
// Each polled tick costs ~13 git subprocesses PER REPO on the server — about
// 150 a minute — almost all re-deriving a byte-identical answer that the store
// then discards on an unchanged signature. A sleeping agent cannot be what
// changed those files.
//
// It backs OFF rather than stopping: diff/tree/comments also change from
// outside the agent (the operator's own terminal in the workspace, server-side
// comment ingestion), and neither routes through the event-driven revalidate.
describe('createTaskCache — polls slower while the agent is asleep', () => {
  function makeLiveness(initial = true) {
    const state = { live: initial };
    return { state, isTaskLive: () => state.live };
  }

  test('an active agent is polled on every tick', async () => {
    const { state, isTaskLive } = makeLiveness(true);
    const { cache, fetchers, poller } = makeCache({ isTaskLive });
    cache.setActiveTask('A'); await flush();
    const before = fetchers.diff.calls;

    poller.fireTick(); await flush();
    poller.fireTick(); await flush();
    expect(fetchers.diff.calls).toBe(before + 2);
    expect(state.live).toBe(true);
  });

  test('a sleeping agent skips ticks until the idle interval elapses', async () => {
    const { isTaskLive } = makeLiveness(false);
    // 5s ticks, 30s idle cadence → one fetch every SIXTH tick.
    const { cache, fetchers, poller, clock } = makeCache({
      isTaskLive, idleIntervalMs: 30000,
    });
    cache.setActiveTask('A'); await flush();
    const before = fetchers.diff.calls;

    for (let i = 0; i < 5; i += 1) { clock.advance(5000); poller.fireTick(); await flush(); }
    expect(fetchers.diff.calls).toBe(before); // 25s in — still nothing

    clock.advance(5000); poller.fireTick(); await flush();
    expect(fetchers.diff.calls).toBe(before + 1); // 30s — one refresh
  });

  test('waking up restores the fast cadence on the very next tick', async () => {
    const { state, isTaskLive } = makeLiveness(false);
    const { cache, fetchers, poller, clock } = makeCache({
      isTaskLive, idleIntervalMs: 30000,
    });
    cache.setActiveTask('A'); await flush();
    poller.fireTick(); await flush();
    const before = fetchers.diff.calls;

    state.live = true; // the agent starts a turn
    poller.fireTick(); await flush();
    poller.fireTick(); await flush();
    expect(fetchers.diff.calls).toBe(before + 2);
  });

  test('going quiet waits a FULL idle interval, not the remainder of one', async () => {
    // Every live tick stamps lastPolledAt, so a task that had been idle 25s,
    // worked for one tick, then slept again waits a fresh 30s — the backoff
    // does not leak away under an agent that stops and starts, which is
    // exactly what an agent does.
    const { state, isTaskLive } = makeLiveness(false);
    const { cache, fetchers, poller, clock } = makeCache({
      isTaskLive, idleIntervalMs: 30000,
    });
    cache.setActiveTask('A'); await flush();
    for (let i = 0; i < 5; i += 1) { clock.advance(5000); poller.fireTick(); await flush(); } // 25s idle

    state.live = true;
    poller.fireTick(); await flush();                                   // one live tick
    state.live = false;
    const before = fetchers.diff.calls;

    for (let i = 0; i < 5; i += 1) { clock.advance(5000); poller.fireTick(); await flush(); }
    expect(fetchers.diff.calls).toBe(before);                           // not 5s later
    clock.advance(5000); poller.fireTick(); await flush();
    expect(fetchers.diff.calls).toBe(before + 1);                       // a full 30s later
  });

  test('an idle task is still polled eventually — the poll is not switched off', async () => {
    // Out-of-band changes (the operator editing in their own terminal, or
    // server-side comment ingestion) have no event to ride, so stopping
    // entirely would leave the panes stale with nothing to refresh them.
    const { isTaskLive } = makeLiveness(false);
    const { cache, fetchers, poller, clock } = makeCache({
      isTaskLive, idleIntervalMs: 30000,
    });
    cache.setActiveTask('A'); await flush();
    const before = fetchers.diff.calls;

    for (let i = 0; i < 18; i += 1) { clock.advance(5000); poller.fireTick(); await flush(); } // 90s
    expect(fetchers.diff.calls).toBe(before + 3);
  });

  test('a hidden stretch counts — returning to the tab refreshes at once', async () => {
    // THE REGRESSION this measures against. createPoller SKIPS the tick
    // entirely while the tab is hidden, so a tick COUNTER stops advancing
    // exactly when the most time is passing. Counting ticks, the operator
    // came back to ten-minute-old file and diff content and then waited a
    // further 30 seconds for it — where before the backoff the wait was 5.
    // Measuring wall time, the catch-up tick createPoller fires on return
    // refreshes immediately.
    const { isTaskLive } = makeLiveness(false);
    const { cache, fetchers, poller, clock } = makeCache({
      isTaskLive, idleIntervalMs: 30000,
    });
    cache.setActiveTask('A'); await flush();
    const before = fetchers.diff.calls;

    // Ten minutes hidden: time passes, no ticks run at all.
    clock.advance(600000);

    // The catch-up tick on return.
    poller.fireTick(); await flush();
    expect(fetchers.diff.calls).toBe(before + 1);
  });

  test('a refresh that just happened is not repeated by the catch-up', async () => {
    // The flip side: returning to a tab that was refreshed a moment ago must
    // not fire a redundant round of git work just because the event arrived.
    const { isTaskLive } = makeLiveness(false);
    const { cache, fetchers, poller, clock } = makeCache({
      isTaskLive, idleIntervalMs: 30000,
    });
    cache.setActiveTask('A'); await flush();
    clock.advance(30000); poller.fireTick(); await flush();
    const before = fetchers.diff.calls;

    clock.advance(1000);
    poller.fireTick(); await flush();
    expect(fetchers.diff.calls).toBe(before);
  });

  test('with no liveness source the cadence is unchanged', async () => {
    // The default is "always active", so any caller that does not inject a
    // predicate keeps the original fixed 5s poll.
    const { cache, fetchers, poller } = makeCache();
    cache.setActiveTask('A'); await flush();
    const before = fetchers.diff.calls;

    poller.fireTick(); await flush();
    poller.fireTick(); await flush();
    expect(fetchers.diff.calls).toBe(before + 2);
  });
});
