// A long git action must keep its spinner across a TAB SWITCH.
//
// The busy flag used to live in this hook's own React state, inside
// SessionHeader. Switching tabs unmounts that header, so the flag went with
// it: the operator clicked "Merge master", moved to another tab and back, and
// the button looked idle while the merge was still running server-side — and
// the obvious next move is to click it again.
//
// ``scope`` moves the flag into the app-global gitActionStore so it outlives
// the component that started it.

import { describe, expect, test, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useBusyAction } from './useBusyAction.js';
import { gitActionKey, gitActionStore } from '../stores/gitActionStore.js';

const SCOPE = gitActionKey('UNA-1', 'merge');

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

describe('useBusyAction — scoped actions survive an unmount', () => {
  beforeEach(() => { gitActionStore._reset(); });

  test('a remount while the action runs still reports busy', async () => {
    const gate = deferred();
    const first = renderHook(() => useBusyAction(() => gate.promise, { scope: SCOPE }));

    await act(async () => { first.result.current[1](); });
    expect(first.result.current[0]).toBe(true);

    // Tab switch: the header unmounts mid-action…
    first.unmount();
    // …and comes back. The action is still running server-side, so the button
    // must still read busy.
    const second = renderHook(() => useBusyAction(() => gate.promise, { scope: SCOPE }));
    expect(second.result.current[0]).toBe(true);

    await act(async () => { gate.resolve({ ok: true }); await gate.promise; });
    expect(second.result.current[0]).toBe(false);
  });

  test('an UNscoped action keeps the old per-component behaviour', async () => {
    const gate = deferred();
    const first = renderHook(() => useBusyAction(() => gate.promise));
    await act(async () => { first.result.current[1](); });
    expect(first.result.current[0]).toBe(true);

    first.unmount();
    const second = renderHook(() => useBusyAction(() => gate.promise));
    // Nothing shared — a Stop button that forgets on unmount is fine, because
    // the thing it was doing is over.
    expect(second.result.current[0]).toBe(false);
    await act(async () => { gate.resolve({ ok: true }); await gate.promise; });
  });

  test('two tasks do not share one spinner', async () => {
    const gate = deferred();
    const a = renderHook(() => useBusyAction(() => gate.promise, {
      scope: gitActionKey('UNA-1', 'merge'),
    }));
    const b = renderHook(() => useBusyAction(() => gate.promise, {
      scope: gitActionKey('UNA-2', 'merge'),
    }));
    await act(async () => { a.result.current[1](); });
    expect(a.result.current[0]).toBe(true);
    expect(b.result.current[0]).toBe(false);
    await act(async () => { gate.resolve({ ok: true }); await gate.promise; });
  });

  test('a throwing action clears the flag instead of sticking forever', async () => {
    // With a SCOPED flag a stuck "busy" is permanent for the session, not just
    // until the next remount — so the failure path has to clear it.
    const { result } = renderHook(() => useBusyAction(
      () => Promise.reject(new Error('boom')), { scope: SCOPE },
    ));
    await act(async () => {
      await result.current[1]().catch(() => {});
    });
    expect(result.current[0]).toBe(false);
    expect(gitActionStore.isBusy(SCOPE)).toBe(false);
  });
});

describe('gitActionStore — a wedged action cannot cost the button forever', () => {
  test('a busy flag ages out', () => {
    // Moving the flag out of the component removed the accidental safety net
    // it had: a hung action left a stuck flag, but a tab switch remounted and
    // cleared it. The flag now outlives the component, so an unsettled
    // promise would disable that button for the rest of the session.
    gitActionStore._reset();
    gitActionStore.setBusy(SCOPE, true);
    expect(gitActionStore.isBusy(SCOPE)).toBe(true);

    const realNow = Date.now;
    try {
      Date.now = () => realNow() + (16 * 60 * 1000);
      expect(gitActionStore.isBusy(SCOPE)).toBe(false);
      expect(gitActionStore.isTaskBusy('UNA-1')).toBe(false);
    } finally {
      Date.now = realNow;
    }
  });
});
