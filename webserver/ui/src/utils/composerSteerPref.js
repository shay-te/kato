// Operator preference: what happens to a chat message sent WHILE Claude is
// mid-turn.
//
//   * steerWhileWorking = true  (default) — HOLD the message in the per-task
//     queue and let it fly when the turn ends. The operator can still click
//     "Steer" on a queued row to promote it mid-turn. This is kato's original
//     behavior; some operators prefer it because it keeps a clean one-message-
//     per-turn cadence and lets them edit/reorder before delivery.
//
//   * steerWhileWorking = false — deliver the message to the live session
//     IMMEDIATELY, even mid-turn, exactly like Claude Code in VS Code: Claude
//     receives it on its next pump while it is still working. No queue, no
//     wait. (The transport already supports this — it's what the Steer button
//     does — this flag just makes it the DEFAULT for the send action.)
//
// A pure client-side UI preference (like the permission-sound / comment-
// collapse prefs), backed by localStorage so it survives reloads. It never
// touches the backend — the delivery mechanism is identical either way; only
// the composer's default decision changes.

import { createPreferenceStore } from './createPreferenceStore.js';

const STORAGE_KEY = 'kato.composerSteer.v1';
// Default TRUE: preserve the existing hold-until-idle behavior for everyone
// who doesn't opt into immediate send, so this change is invisible until an
// operator flips it.
const DEFAULT_STEER_WHILE_WORKING = true;

// The store persists a record; this module's public surface is the single
// flag inside it. See createPreferenceStore for why a bare boolean cannot be
// the stored shape.
const _store = createPreferenceStore({
  key: STORAGE_KEY,
  defaults: { steerWhileWorking: DEFAULT_STEER_WHILE_WORKING },
  coerce: (parsed, defaults) => ({
    steerWhileWorking: parsed.steerWhileWorking === undefined
      ? defaults.steerWhileWorking
      : !!parsed.steerWhileWorking,
  }),
});

export function readSteerWhileWorking() {
  return _store.read().steerWhileWorking;
}

export function writeSteerWhileWorking(next) {
  return _store.write({ steerWhileWorking: !!next }).steerWhileWorking;
}

export function subscribeSteerWhileWorking(fn) {
  return _store.subscribe((record) => fn(record.steerWhileWorking));
}

// Test-only: the cache + listeners are module-level, so tests must reset
// between cases for isolation.
export function _resetSteerWhileWorkingPref() {
  _store.reset();
}
