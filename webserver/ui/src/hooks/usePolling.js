import { useEffect, useRef } from 'react';
import { createPoller } from '../stores/createPoller.js';

// Run ``fn`` immediately, then every ``intervalMs`` until unmount — SKIPPING
// ticks while the tab is hidden. ``deps`` are the values that should restart
// polling when they change (like a useEffect dependency array — e.g.
// ``[taskId]``). Pass ``{ enabled: false }`` to skip polling entirely (e.g.
// no task yet). ``fn`` is read through a ref so an inline/unstable callback
// doesn't thrash the loop on every render.
//
// The loop itself is ``createPoller`` — deliberately not a second copy of it.
// That module's header describes three hand-rolled copies of this same loop
// that had already drifted, "one used setInterval"; this hook WAS that copy,
// and the drift was the part that matters: no ``document.hidden`` check. Five
// hooks poll through here (sessions, config-status, plan, push-approval,
// safety) plus the permissions settings panel, so a backgrounded kato window
// kept issuing ~64 requests a minute, indefinitely, with nobody looking at
// the answers — and every ``/api/sessions`` tick runs a full live-session
// walk plus permission auto-resolve on the server.
//
// The visibility skip AND the catch-up tick on return both live in
// ``createPoller`` — this hook adds only the React lifecycle and the immediate
// first read. Registering a second ``visibilitychange`` listener here would
// double-fire the catch-up, which is how the drift this consolidation undid
// started in the first place.
export function usePolling(fn, intervalMs, deps = [], { enabled = true } = {}) {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) { return undefined; }
    let cancelled = false;
    const run = () => { if (!cancelled) { fnRef.current(); } };

    const poller = createPoller(run, intervalMs);
    // First read is immediate — the loop's first tick is one interval away.
    run();
    poller.start();

    return () => {
      cancelled = true;
      poller.stop();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, enabled, ...deps]);
}
