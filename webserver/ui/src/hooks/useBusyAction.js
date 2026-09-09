import { useCallback, useRef, useState, useSyncExternalStore } from 'react';
import { gitActionStore } from '../stores/gitActionStore.js';

// Wraps an async action with an in-flight ``busy`` flag so a double-tap
// can't fire it twice. ``run(...args)`` no-ops while busy or when
// ``enabled`` is false; otherwise it does
//   setBusy(true) → await action(...args) → setBusy(false) → onDone(result) → return result
// (the exact order the hand-written push/pull/approve callbacks used).
// ``action`` and ``onDone`` are read through refs so ``run`` stays
// referentially stable — it only changes identity when busy/enabled flip.
//
// ``scope`` moves the flag OUT of this component and into the app-global
// ``gitActionStore``. Local state dies with the component: switching tabs
// unmounts SessionHeader, so a long git action lost its spinner while the
// action was still running server-side, and the operator's only evidence of
// it disappeared. A scoped action survives the unmount and reappears on the
// way back. Unscoped callers keep plain local state — a Stop button that
// forgets on unmount is fine, because the thing it was doing is over.
export function useBusyAction(action, { enabled = true, onDone, scope = '' } = {}) {
  const [localBusy, setLocalBusy] = useState(false);
  const storeBusy = useSyncExternalStore(
    gitActionStore.subscribe,
    () => gitActionStore.isBusy(scope),
    () => false,
  );
  const busy = scope ? storeBusy : localBusy;

  const actionRef = useRef(action);
  actionRef.current = action;
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;

  const setBusy = useCallback((next) => {
    if (scopeRef.current) { gitActionStore.setBusy(scopeRef.current, next); }
    else { setLocalBusy(next); }
  }, []);

  const run = useCallback(async (...args) => {
    if (busy || !enabled) { return null; }
    setBusy(true);
    try {
      const result = await actionRef.current(...args);
      // Clear BEFORE ``onDone`` — the order every hand-written push/pull/
      // approve callback used, and ``onDone`` triggers refetches that read
      // this flag.
      setBusy(false);
      if (onDoneRef.current) { onDoneRef.current(result); }
      return result;
    } finally {
      // Idempotent second call, for the throwing path only: an action that
      // raised used to leave the flag stuck on — and with a SCOPED flag that
      // outlives the component, stuck means stuck for the whole session, not
      // just until the next remount.
      setBusy(false);
    }
  }, [busy, enabled, setBusy]);

  return [busy, run];
}
