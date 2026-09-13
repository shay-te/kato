// Child-engine tests — NO mocks. A real Zustand store, a real in-memory
// fetcher (a plain async fn whose payload/gate/failure we control), and real
// deferred promises to drive single-flight / SWR / error paths deterministically
// without a network or fake timers.

import { describe, test, expect } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

import { createDataStore } from './createDataStore.js';

const flush = () => new Promise((r) => setTimeout(r, 0));

function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

// A REAL fetcher: returns whatever `payload` is set to, counts calls, and can
// be gated (held mid-flight) or made to fail. Not a spy — we inspect real state.
function makeFetcher(payload) {
  const s = { payload, calls: 0, gate: null, fail: null };
  const fetch = async () => {
    s.calls += 1;
    if (s.gate) { await s.gate.promise; }
    if (s.fail) { throw new Error(s.fail); }
    return s.payload;
  };
  return { fetch, s };
}

const identity = (x) => x;
const clone = (x) => ({ ...x });


describe('createDataStore — child engine (store logic)', () => {
  test('load fetches, parses, and commits ready', async () => {
    const { fetch, s } = makeFetcher({ v: 1 });
    const child = createDataStore({ fetch, parse: identity, empty: null });
    await child.load('T1');
    expect(child.get('T1')).toMatchObject({ data: { v: 1 }, status: 'ready', error: '' });
    expect(s.calls).toBe(1);
  });

  test('first load shows loading; a revalidate over data never blanks it (SWR)', async () => {
    const { fetch, s } = makeFetcher({ v: 1 });
    s.gate = deferred();
    const child = createDataStore({ fetch, parse: identity, empty: null });
    const p = child.load('T1');
    expect(child.get('T1').status).toBe('loading');       // first load = spinner
    s.gate.resolve(); await p;
    expect(child.get('T1').status).toBe('ready');

    const gate2 = deferred(); s.gate = gate2;
    const p2 = child.load('T1');
    expect(child.get('T1').status).toBe('ready');          // stays ready (SWR)
    gate2.resolve(); await p2;
    expect(child.get('T1').status).toBe('ready');
  });

  test('single-flight coalesces concurrent loads into ONE fetch', async () => {
    const { fetch, s } = makeFetcher({ v: 1 });
    s.gate = deferred();
    const child = createDataStore({ fetch, parse: identity, empty: null });
    const a = child.load('T1');
    const b = child.load('T1');
    expect(a).toBe(b);                                     // same in-flight promise
    await flush();                                         // let the ONE fetch start (gated)
    expect(s.calls).toBe(1);
    s.gate.resolve(); await Promise.all([a, b]);
  });

  test('unchanged bytes keep the SAME parsed data reference', async () => {
    const { fetch } = makeFetcher({ v: 1 });
    const child = createDataStore({ fetch, parse: clone, empty: null });
    await child.load('T1');
    const first = child.get('T1').data;
    await child.load('T1');                                // identical bytes
    expect(child.get('T1').data).toBe(first);              // referential stability
    expect(child.get('T1').status).toBe('ready');
  });

  test('changed bytes produce a new parsed data reference', async () => {
    const { fetch, s } = makeFetcher({ v: 1 });
    const child = createDataStore({ fetch, parse: clone, empty: null });
    await child.load('T1');
    const first = child.get('T1').data;
    s.payload = { v: 2 };
    await child.load('T1');
    expect(child.get('T1').data).not.toBe(first);
    expect(child.get('T1').data).toEqual({ v: 2 });
  });

  test('error keeps the last-known data and surfaces the message', async () => {
    const { fetch, s } = makeFetcher({ v: 1 });
    const child = createDataStore({ fetch, parse: identity, empty: null });
    await child.load('T1');
    const good = child.get('T1').data;
    s.fail = 'boom';
    await child.load('T1');
    expect(child.get('T1').data).toBe(good);               // last data retained
    expect(child.get('T1').status).toBe('error');
    expect(child.get('T1').error).toBe('boom');
  });

  test('purge drops the task entirely and resets dedupe', async () => {
    const { fetch, s } = makeFetcher({ v: 1 });
    const child = createDataStore({ fetch, parse: identity, empty: null });
    await child.load('T1');
    child.purge('T1');
    expect(child.get('T1')).toBe(null);
    expect(child.has('T1')).toBe(false);
    await child.load('T1');                                // re-fetches (sig reset)
    expect(s.calls).toBe(2);
    expect(child.get('T1').status).toBe('ready');
  });

  test('two tasks are independent — one load never touches the other', async () => {
    const { fetch, s } = makeFetcher({ v: 1 });
    const child = createDataStore({ fetch, parse: clone, empty: null });
    await child.load('A');
    const aData = child.get('A').data;
    s.payload = { v: 9 };
    await child.load('B');
    expect(child.get('A').data).toBe(aData);               // A untouched
    expect(child.get('B').data).toEqual({ v: 9 });
  });
});


describe('createDataStore.use — React binding', () => {
  const map = (sl) => ({
    data: sl.data,
    loading: sl.status === 'idle' || sl.status === 'loading',
    error: sl.error,
  });

  test('a task with no entry reads as loading (idle) with the empty data', () => {
    const { fetch } = makeFetcher([]);
    const child = createDataStore({ fetch, parse: identity, empty: [] });
    const { result } = renderHook(() => child.use('T1', map));
    expect(result.current.loading).toBe(true);
    expect(result.current.data).toEqual([]);
  });

  test('an unchanged-bytes revalidate causes ZERO extra renders', async () => {
    const { fetch } = makeFetcher({ v: 1 });
    const child = createDataStore({ fetch, parse: clone, empty: null });
    let renders = 0;
    const { result } = renderHook(() => { renders += 1; return child.use('T1', map); });

    await act(async () => { await child.load('T1'); });
    await waitFor(() => expect(result.current.loading).toBe(false));
    const rendersAfterLoad = renders;
    const dataRef = result.current.data;

    await act(async () => { await child.load('T1'); });    // same bytes, lastFetched bumps
    expect(renders).toBe(rendersAfterLoad);                // useShallow bails
    expect(result.current.data).toBe(dataRef);
  });
});


// A REAL in-memory adapter with the same contract as ./persistedPayloads.js
// (read / write / forget over stored TEXT). Gated so a test can hold the
// restore mid-read and let the fetch win the race.
//
// ``read`` snapshots the value at CALL time, matching a real IndexedDB
// readonly transaction — it sees the store as it was when the read was issued,
// not after a later write commits. Re-reading the map on resume instead made
// the gated race test tautological: the fetch's own write-back had already
// replaced the stale value it was supposed to prove gets dropped.
function makePersist(seed = {}) {
  const s = { store: new Map(Object.entries(seed)), gate: null, reads: 0, writes: [] };
  const persist = {
    read: async (taskId) => {
      s.reads += 1;
      const snapshot = s.store.get(taskId);
      if (s.gate) { await s.gate.promise; }
      return snapshot;
    },
    write: async (taskId, text) => { s.writes.push(taskId); s.store.set(taskId, text); },
    forget: async (taskId) => { s.store.delete(taskId); },
  };
  return { persist, s };
}

describe('createDataStore — durable payloads (reload is instant)', () => {
  test('a stored payload paints BEFORE the fetch lands', async () => {
    const { fetch, s } = makeFetcher({ v: 2 });
    s.gate = deferred();                                   // fetch held open
    const { persist } = makePersist({ T1: JSON.stringify({ v: 1 }) });
    const child = createDataStore({ fetch, parse: clone, empty: null, persist });

    const p = child.load('T1');
    await flush();
    expect(child.get('T1')).toMatchObject({ data: { v: 1 }, status: 'ready' });

    s.gate.resolve(); await p;                             // fresh data still wins in the end
    expect(child.get('T1').data).toEqual({ v: 2 });
  });

  test('the restore adopts the stored bytes as the signature — an identical fetch re-renders nothing', async () => {
    const { fetch } = makeFetcher({ v: 1 });
    let parses = 0;
    const parse = (x) => { parses += 1; return { ...x }; };
    const { persist } = makePersist({ T1: JSON.stringify({ v: 1 }) });
    const child = createDataStore({ fetch, parse, empty: null, persist });

    await child.load('T1');
    await flush();
    const restored = child.get('T1').data;
    expect(parses).toBe(1);                                // parsed for the restore ONLY
    expect(child.get('T1').data).toBe(restored);           // fetch found identical bytes
  });

  test('a fetch that wins the race is never overwritten by the restore', async () => {
    const { fetch } = makeFetcher({ v: 'fresh' });
    const { persist, s } = makePersist({ T1: JSON.stringify({ v: 'stale' }) });
    s.gate = deferred();                                   // restore held open
    const child = createDataStore({ fetch, parse: clone, empty: null, persist });

    await child.load('T1');                                // fetch lands first
    expect(child.get('T1').data).toEqual({ v: 'fresh' });
    s.gate.resolve(); await flush();
    expect(child.get('T1').data).toEqual({ v: 'fresh' });  // stale restore dropped
  });

  test('a task purged mid-restore is never resurrected', async () => {
    const { fetch } = makeFetcher({ v: 1 });
    const { persist, s } = makePersist({ T1: JSON.stringify({ v: 1 }) });
    s.gate = deferred();
    const child = createDataStore({ fetch, parse: clone, empty: null, persist });

    const p = child.load('T1');
    child.purge('T1');
    s.gate.resolve(); await flush(); await p;
    expect(child.get('T1')).toBe(null);
  });

  test('a corrupt stored payload is ignored, not thrown', async () => {
    const { fetch, s } = makeFetcher({ v: 1 });
    s.gate = deferred();
    const { persist } = makePersist({ T1: 'not json{' });
    const child = createDataStore({ fetch, parse: clone, empty: null, persist });

    const p = child.load('T1');
    await flush();
    expect(child.get('T1').status).toBe('loading');        // no restore, no crash
    s.gate.resolve(); await p;
    expect(child.get('T1').data).toEqual({ v: 1 });
  });

  test('storage is read at most once per task', async () => {
    const { fetch } = makeFetcher({ v: 1 });
    const { persist, s } = makePersist();
    const child = createDataStore({ fetch, parse: clone, empty: null, persist });
    await child.load('T1');
    await child.load('T1');
    await child.load('T1');
    expect(s.reads).toBe(1);
  });

  test('only CHANGED bytes are written back', async () => {
    const { fetch, s: f } = makeFetcher({ v: 1 });
    const { persist, s } = makePersist();
    const child = createDataStore({ fetch, parse: clone, empty: null, persist });

    await child.load('T1');
    expect(s.writes).toEqual(['T1']);
    expect(s.store.get('T1')).toBe(JSON.stringify({ v: 1 }));

    await child.load('T1');                                // identical bytes
    expect(s.writes).toEqual(['T1']);                      // no second write

    f.payload = { v: 2 };
    await child.load('T1');
    expect(s.writes).toEqual(['T1', 'T1']);
    expect(s.store.get('T1')).toBe(JSON.stringify({ v: 2 }));
  });

  test('LRU eviction keeps the durable copy; forgetting the task drops it', async () => {
    const { fetch } = makeFetcher({ v: 1 });
    const { persist, s } = makePersist();
    const child = createDataStore({ fetch, parse: clone, empty: null, persist });

    await child.load('T1');
    child.purge('T1');                                     // memory only
    await flush();
    expect(s.store.has('T1')).toBe(true);

    await child.load('T1');
    child.purge('T1', { persisted: true });                // operator forgot it
    await flush();
    expect(s.store.has('T1')).toBe(false);
  });

  test('a re-visited task restores again after eviction', async () => {
    const { fetch, s: f } = makeFetcher({ v: 1 });
    const { persist, s } = makePersist();
    const child = createDataStore({ fetch, parse: clone, empty: null, persist });
    await child.load('T1');
    child.purge('T1');

    f.gate = deferred();
    const p = child.load('T1');
    await flush();
    expect(child.get('T1')).toMatchObject({ data: { v: 1 }, status: 'ready' });
    expect(s.reads).toBe(2);                               // hydrate flag cleared by purge
    f.gate.resolve(); await p;
  });
});
