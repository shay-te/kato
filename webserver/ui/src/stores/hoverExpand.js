import { useEffect, useState } from 'react';
import { createPubSub } from './pubsub.js';

// A panel that sits collapsed at the edge and EXPANDS while it is pointed at:
// the tasks menu on the left, the files tree beside a maximized centre. One
// behaviour for both, so they feel alike and cannot drift apart.
//
// Both waits are short on purpose:
//   - opening waits ``HOVER_EXPAND_OPEN_DELAY_MS``: the panel sits at an edge,
//     and a pointer only passing over it must not throw a panel over the page;
//   - closing waits ``HOVER_EXPAND_CLOSE_GRACE_MS``: a pointer that slips off
//     the edge for a moment must not snap it shut. Entering again in time
//     keeps it open.
export const HOVER_EXPAND_OPEN_DELAY_MS = 150;
export const HOVER_EXPAND_CLOSE_GRACE_MS = 220;

export function createHoverExpand({
  openDelayMs = HOVER_EXPAND_OPEN_DELAY_MS,
  closeGraceMs = HOVER_EXPAND_CLOSE_GRACE_MS,
} = {}) {
  let open = false;
  let timer = null;
  const pubsub = createPubSub(() => open);

  function set(next) {
    if (open === next) { return; }
    open = next;
    pubsub.emit();
  }

  function cancelPending() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function setSoon(next, delayMs) {
    cancelPending();
    timer = setTimeout(() => {
      timer = null;
      set(next);
    }, delayMs);
  }

  return {
    isOpen: () => open,
    // At once — pointing at the panel's own handle is asking.
    open() {
      cancelPending();
      set(true);
    },
    // Already open: stays open (and a pending close is called off).
    openSoon() {
      if (open) {
        cancelPending();
        return;
      }
      setSoon(true, openDelayMs);
    },
    closeSoon() {
      setSoon(false, closeGraceMs);
    },
    close() {
      cancelPending();
      set(false);
    },
    subscribe: pubsub.subscribe,
    // Test-only: collapsed, nothing pending (subscribers are kept).
    reset() {
      cancelPending();
      open = false;
    },
  };
}

// Whether ``panel`` is expanded, live.
export function useHoverExpanded(panel) {
  const [value, setValue] = useState(panel.isOpen());
  useEffect(() => panel.subscribe(setValue), [panel]);
  return value;
}
