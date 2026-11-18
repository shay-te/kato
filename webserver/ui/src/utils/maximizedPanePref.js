// Operator preference: which pane, if any, has the whole window.
//
// Normally kato shows three panes — the files tree, the file/diff view in the
// centre, and the chat. Maximizing one collapses the other two: the chat, to
// read a long transcript or a wide tool output; the centre, to read a file or
// a diff at full width. ONE value, so the two can never both be maximized and
// one button restores whichever is.
//
// A pure client-side view preference, persisted per browser and never sent to
// the backend. Persisted rather than reset-on-reload because it is a reading
// posture: an operator who maximized to read has not finished just because
// they refreshed.
//
// A module store, not App state: the toggles sit in the chat header (inside a
// component App remounts on every task switch), the tab strip and the plan
// view, while the pane GRID they control belongs to Layout.

import { createPreferenceStore } from './createPreferenceStore.js';

export const MAXIMIZED_PANE = Object.freeze({ NONE: '', CHAT: 'chat', CENTER: 'center' });

const PANES = new Set([MAXIMIZED_PANE.CHAT, MAXIMIZED_PANE.CENTER]);

// The key the chat-only preference used: a chat maximized before this opens
// maximized still (its old ``{chatMaximized: true}`` reads as the chat).
const STORAGE_KEY = 'kato.chatMaximized.v1';

const _store = createPreferenceStore({
  key: STORAGE_KEY,
  // Nothing maximized: the three-pane layout is what kato has always opened
  // on, and a view preference should never surprise someone who has not
  // asked for it.
  defaults: { pane: MAXIMIZED_PANE.NONE },
  coerce: (parsed, defaults) => {
    if (PANES.has(parsed.pane)) { return { pane: parsed.pane }; }
    if (parsed.pane === undefined && parsed.chatMaximized === true) { return { pane: MAXIMIZED_PANE.CHAT }; }
    return { pane: defaults.pane };
  },
});

export function readMaximizedPane() {
  return _store.read().pane;
}

export function writeMaximizedPane(pane) {
  return _store.write({ pane }).pane;
}

// Maximize ``pane``, or restore the panes when it already is.
export function toggleMaximizedPane(pane) {
  return writeMaximizedPane(readMaximizedPane() === pane ? MAXIMIZED_PANE.NONE : pane);
}

export function subscribeMaximizedPane(fn) {
  return _store.subscribe((record) => fn(record.pane));
}

// Test-only: the cache + listeners are module-level, so tests must reset
// between cases for isolation.
export function _resetMaximizedPanePref() {
  _store.reset();
}
