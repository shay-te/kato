// Visibility-aware self-rescheduling poller for the module-level stores
// (commentStore, permissionStore, diffStore).
//
// Before this, each store hand-rolled the SAME loop: a recursive
// ``setTimeout`` (not ``setInterval`` — so a slow tick can't pile up
// overlapping fetches) that skips the tick while the tab is hidden (so a
// backgrounded kato tab doesn't keep hammering the server). Three copies
// had already drifted (one used ``setInterval``). This is that loop, once.
//
// ``start()`` is idempotent (a second call while running is a no-op);
// ``stop()`` cancels the pending tick. The store owns WHEN to start/stop
// (typically: start on the first subscriber, stop on the last).
export function createPoller(tick, intervalMs) {
  let timer = null;
  function loop() {
    timer = setTimeout(() => {
      if (typeof document === 'undefined' || !document.hidden) {
        tick();
      }
      loop();
    }, intervalMs);
  }

  // Returning to the tab ticks IMMEDIATELY rather than waiting out whatever
  // is left of the interval. Skipping ticks while hidden is only half the
  // deal: without this, going quiet also means being stale at the exact
  // moment someone looks — and for the task-cache poller "stale" is the file
  // tree and the diff, i.e. the operator reading last week's code.
  function onVisibilityChange() {
    if (timer === null) { return; }
    if (typeof document !== 'undefined' && document.hidden) { return; }
    tick();
  }

  // Resolved per call, never cached: tests swap ``document`` for a bare
  // ``{ hidden }`` object AFTER the poller is built, and a cached reference
  // would then call ``removeEventListener`` on something that has none.
  function listen(method) {
    if (typeof document === 'undefined') { return; }
    const fn = document[method];
    if (typeof fn === 'function') {
      fn.call(document, 'visibilitychange', onVisibilityChange);
    }
  }

  return {
    start() {
      if (timer !== null) { return; }
      loop();
      listen('addEventListener');
    },
    stop() {
      if (timer === null) { return; }
      clearTimeout(timer);
      timer = null;
      listen('removeEventListener');
    },
    get running() { return timer !== null; },
  };
}
