import { useEffect, useRef } from 'react';

// How fast the title alternates. Fast enough to read as motion in a strip
// of tab titles, slow enough that each half is actually legible.
export const TITLE_FLASH_MS = 1200;

/**
 * Flash the browser tab title while something needs the operator.
 *
 * kato already fires a desktop notification, but notifications get
 * missed — dismissed by accident, suppressed by focus assist, or simply
 * gone by the time the operator looks back. The tab title is the one
 * surface that keeps saying it: an agent sits blocked on an approval for
 * as long as it takes someone to notice, so "you missed it" costs real
 * wall-clock time.
 *
 * Only flashes while the tab is HIDDEN. With kato in front the approval
 * dialog is right there — animating the title as well would be noise, and
 * noise is what teaches people to stop reading it.
 *
 * The base title is captured lazily and restored on stop, so a static
 * ``<title>`` in the template needs no cooperation from this hook.
 */
export function useTitleAlert(active, message, intervalMs = TITLE_FLASH_MS) {
  const baseTitleRef = useRef('');
  const timerRef = useRef(null);

  useEffect(() => {
    if (typeof document === 'undefined') { return undefined; }

    function stop() {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      if (baseTitleRef.current) {
        document.title = baseTitleRef.current;
        baseTitleRef.current = '';
      }
    }

    function start() {
      if (timerRef.current) { return; }
      // Captured at START, not at mount: the title may legitimately change
      // while nothing is pending, and restoring a stale one would be worse
      // than not restoring at all.
      baseTitleRef.current = document.title;
      let showingAlert = false;
      document.title = message;
      showingAlert = true;
      timerRef.current = setInterval(() => {
        showingAlert = !showingAlert;
        document.title = showingAlert ? message : baseTitleRef.current;
      }, intervalMs);
    }

    function sync() {
      if (active && document.hidden) {
        start();
      } else {
        stop();
      }
    }

    sync();
    // Coming back to the tab must restore the title immediately — leaving
    // "Approval needed" in a tab the operator is looking at reads as a
    // second, phantom request.
    document.addEventListener('visibilitychange', sync);
    return () => {
      document.removeEventListener('visibilitychange', sync);
      stop();
    };
  }, [active, message, intervalMs]);
}
