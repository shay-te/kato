// How many reviews a review loop may run — the operator's pick, remembered.
//
// Chosen in the review loop view before Start / Run again and kept for the
// next loop on any task, so the operator sets it once and it stays. The
// server holds the same range (review_loop_core_lib → MAX_ROUNDS_LIMIT) and
// refuses anything outside it.
import { useSyncExternalStore } from 'react';
import { createPreferenceStore } from '../../utils/createPreferenceStore.js';

export const REVIEW_LOOP_ROUNDS = Object.freeze({ MIN: 1, MAX: 30, DEFAULT: 5 });

export const REVIEW_LOOP_ROUND_CHOICES = Object.freeze(Array.from(
  { length: REVIEW_LOOP_ROUNDS.MAX - REVIEW_LOOP_ROUNDS.MIN + 1 },
  (_unused, index) => REVIEW_LOOP_ROUNDS.MIN + index,
));

// A whole number in range, or the default: a stored value from an older build
// or a hand edit must never send the server a limit it refuses.
export function coerceReviewLoopRounds(value) {
  const rounds = typeof value === 'number' ? value : Number.parseInt(String(value ?? ''), 10);
  if (!Number.isInteger(rounds)) { return REVIEW_LOOP_ROUNDS.DEFAULT; }
  return Math.min(REVIEW_LOOP_ROUNDS.MAX, Math.max(REVIEW_LOOP_ROUNDS.MIN, rounds));
}

const _store = createPreferenceStore({
  key: 'kato.reviewLoopRounds.v1',
  defaults: { rounds: REVIEW_LOOP_ROUNDS.DEFAULT },
  coerce: (parsed) => ({ rounds: coerceReviewLoopRounds(parsed.rounds) }),
});

export function readReviewLoopRounds() {
  return _store.read().rounds;
}

// Takes the select's string as well as a number; ``coerce`` normalises it.
export function writeReviewLoopRounds(rounds) {
  return _store.write({ rounds }).rounds;
}

export function useReviewLoopRounds() {
  return useSyncExternalStore(_store.subscribe, readReviewLoopRounds, readReviewLoopRounds);
}

// Test-only: the cache and listeners are module-level.
export function _resetReviewLoopRounds() {
  _store.reset();
}
