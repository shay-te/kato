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
  return {
    start() { if (timer === null) { loop(); } },
    stop() {
      if (timer !== null) { clearTimeout(timer); timer = null; }
    },
    get running() { return timer !== null; },
  };
}
