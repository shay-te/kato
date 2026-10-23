// Operator preference: does the Usage dialog list tasks that were deleted?
//
// A deleted task's transcripts go with it, so kato keeps only its token
// totals (see usage_history_store.py) and the dialog offers them behind a
// switch. Default OFF: the dialog opens on what exists now, and the history is
// something the operator asks for — and, having asked, expects to find still
// on the next time they open it.
//
// A pure client view preference, kept per browser like the other ones.
import { useSyncExternalStore } from 'react';
import { createPreferenceStore } from './createPreferenceStore.js';

const _store = createPreferenceStore({
  key: 'kato.usageShowDeleted.v1',
  defaults: { showDeleted: false },
  coerce: (parsed, defaults) => ({
    showDeleted: parsed.showDeleted === undefined ? defaults.showDeleted : !!parsed.showDeleted,
  }),
});

export function readUsageShowDeleted() {
  return _store.read().showDeleted;
}

export function writeUsageShowDeleted(next) {
  return _store.write({ showDeleted: !!next }).showDeleted;
}

export function useUsageShowDeleted() {
  return useSyncExternalStore(_store.subscribe, readUsageShowDeleted, readUsageShowDeleted);
}

// Test-only: the cache and listeners are module-level.
export function _resetUsageShowDeleted() {
  _store.reset();
}
