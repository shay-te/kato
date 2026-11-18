// The tasks menu's status filter, remembered per browser (the state picked, or
// '' for all) — the same mechanism as the other view preferences.

import { createPreferenceStore } from './createPreferenceStore.js';

const _store = createPreferenceStore({
  key: 'kato.taskStateFilter.v1',
  defaults: { state: '' },
  coerce: (parsed, defaults) => ({
    state: typeof parsed.state === 'string' ? parsed.state.trim() : defaults.state,
  }),
});

export function readTicketStateFilter() {
  return _store.read().state;
}

export function writeTicketStateFilter(state) {
  return _store.write({ state: String(state || '') }).state;
}

export function subscribeTicketStateFilter(fn) {
  return _store.subscribe((record) => fn(record.state));
}

// Test-only: the cache + listeners are module-level.
export function _resetTicketStateFilter() {
  _store.reset();
}
