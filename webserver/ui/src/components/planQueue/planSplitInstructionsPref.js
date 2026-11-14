// How the operator wants a plan split, in their own words — remembered.
//
// Typed in the plan tab before "Propose a split of plan.md" and kept for the
// next split on any task (like the review loop's pickers), so a standing
// preference — "at most 4 tasks, not one per file" — is written once. It is
// shown in the box every time, so nothing rides along unseen. The server
// keeps the same limit (plan_queue_core_lib → MAX_SPLIT_INSTRUCTIONS_CHARS;
// a test pins the two equal).
import { useSyncExternalStore } from 'react';
import { createPreferenceStore } from '../../utils/createPreferenceStore.js';

export const MAX_SPLIT_INSTRUCTIONS_CHARS = 4000;

const _store = createPreferenceStore({
  key: 'kato.planSplitInstructions.v1',
  defaults: { text: '' },
  coerce: (parsed) => ({
    text: typeof parsed.text === 'string' ? parsed.text.slice(0, MAX_SPLIT_INSTRUCTIONS_CHARS) : '',
  }),
});

export function readSplitInstructions() {
  return _store.read().text;
}

export function writeSplitInstructions(text) {
  return _store.write({ text }).text;
}

export function useSplitInstructions() {
  return useSyncExternalStore(_store.subscribe, readSplitInstructions, readSplitInstructions);
}

// Test-only: the cache and listeners are module-level.
export function _resetSplitInstructions() {
  _store.reset();
}
