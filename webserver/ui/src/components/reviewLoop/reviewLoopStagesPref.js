// Which optional stages a review loop runs — the operator's picks, remembered.
//
// Ticked in the review loop view before Start / Run again and kept for every
// later loop, on any task (same model as the round limit beside them). All
// on by default: a self-check in the main chat first, tests that must pass,
// a clean-room check after the fixes, and one more sweep after any clean
// review ("it was clean, we ran it again, and it found a MAJOR").
import { useSyncExternalStore } from 'react';
import { createPreferenceStore } from '../../utils/createPreferenceStore.js';

export const REVIEW_LOOP_STAGES = Object.freeze(
  ['self_check', 'verify_tests', 'confirm_clean', 'extra_sweep'],
);

const _store = createPreferenceStore({
  key: 'kato.reviewLoopStages.v1',
  defaults: Object.fromEntries(REVIEW_LOOP_STAGES.map((name) => [name, true])),
  // Anything but an explicit false is on, so a stage added later starts on.
  coerce: (parsed) => Object.fromEntries(
    REVIEW_LOOP_STAGES.map((name) => [name, parsed[name] !== false]),
  ),
});

export function readReviewLoopStages() {
  return _store.read();
}

export function writeReviewLoopStage(name, on) {
  return _store.write({ ..._store.read(), [name]: !!on });
}

export function useReviewLoopStages() {
  return useSyncExternalStore(_store.subscribe, readReviewLoopStages, readReviewLoopStages);
}

// Test-only: the cache and listeners are module-level.
export function _resetReviewLoopStages() {
  _store.reset();
}
