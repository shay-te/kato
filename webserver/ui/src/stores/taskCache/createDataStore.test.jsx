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
