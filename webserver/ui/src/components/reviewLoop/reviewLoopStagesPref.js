// Which optional stages a review loop runs — the operator's picks, remembered.
//
// Ticked in the review loop view before Start / Run again and kept for every
// later loop, on any task (same model as the round limit beside them). On by
// default: tests that must pass, a clean-room check after the fixes, and one
// more sweep after any clean review ("it was clean, we ran it again, and it
// found a MAJOR"). The self-check in the main chat is OFF unless ticked — the
// operator's call. The server applies the same defaults to a request that
// leaves a stage out (review_loop_routes._STAGE_DEFAULTS, pinned by a test).
import { useSyncExternalStore } from 'react';
import { createPreferenceStore } from '../../utils/createPreferenceStore.js';

export const REVIEW_LOOP_STAGES = Object.freeze(
  ['self_check', 'verify_tests', 'confirm_clean', 'extra_sweep'],
);

export const REVIEW_LOOP_STAGE_DEFAULTS = Object.freeze({
  self_check: false,
  verify_tests: true,
  confirm_clean: true,
  extra_sweep: true,
});

const _store = createPreferenceStore({
  // v2: every v1 record was written whole, so it held ``self_check: true``
  // explicitly and the self-check's new default could never show.
  key: 'kato.reviewLoopStages.v2',
  defaults: REVIEW_LOOP_STAGE_DEFAULTS,
  // A stored true / false is the operator's pick; anything else takes the
  // stage's default (a stage added later starts at its own).
  coerce: (parsed) => Object.fromEntries(REVIEW_LOOP_STAGES.map((name) => [
    name, typeof parsed[name] === 'boolean' ? parsed[name] : REVIEW_LOOP_STAGE_DEFAULTS[name],
  ])),
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
