// Operator preference: is the chat pane maximized to the full window?
//
// Normally kato shows three panes — the files tree, the file/diff preview, and
// the chat. Maximizing collapses the first two so the conversation gets the
// whole width, for reading a long transcript or a wide tool output without
// dragging the resizer to the edge and back.
//
// A pure client-side view preference, like the steer and comment-collapse
// prefs, so it is persisted per browser and never touches the backend.
// Persisted rather than reset-on-reload because it is a reading posture: an
// operator who maximized to read a transcript has not finished reading it just
// because they refreshed.
//
// It lives in a store rather than in App's state because the TOGGLE sits in
// the chat header (AgentBackendTabs, inside SessionDetail, which App keys on
// the active task and therefore remounts on every tab switch) while the pane
// GRID it controls belongs to Layout. Threading a callback down through a
// component that remounts, to control a sibling's layout, is the prop-drilling
// this codebase already answers with a module store — the same shape as
// activeBackendStore and agentStatusStore.

import { createPreferenceStore } from './createPreferenceStore.js';

const STORAGE_KEY = 'kato.chatMaximized.v1';

// Default FALSE: the three-pane layout is what kato has always opened on, and
// a view preference should never surprise someone who has not asked for it.
const DEFAULT_CHAT_MAXIMIZED = false;

const _store = createPreferenceStore({
  key: STORAGE_KEY,
  defaults: { chatMaximized: DEFAULT_CHAT_MAXIMIZED },
  coerce: (parsed, defaults) => ({
    chatMaximized: parsed.chatMaximized === undefined
      ? defaults.chatMaximized
      : !!parsed.chatMaximized,
  }),
});

export function readChatMaximized() {
  return _store.read().chatMaximized;
}

export function writeChatMaximized(next) {
  return _store.write({ chatMaximized: !!next }).chatMaximized;
}

export function toggleChatMaximized() {
  return writeChatMaximized(!readChatMaximized());
}

export function subscribeChatMaximized(fn) {
  return _store.subscribe((record) => fn(record.chatMaximized));
}

// Test-only: the cache + listeners are module-level, so tests must reset
// between cases for isolation.
export function _resetChatMaximizedPref() {
  _store.reset();
}
