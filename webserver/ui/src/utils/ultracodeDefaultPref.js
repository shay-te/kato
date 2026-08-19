// Operator preference: should the composer's "ultracode" chip start ON for a
// task that has never been toggled?
//
// The chip itself is PER-TASK (utils/composerDraft.js) — that is the right
// granularity for a keyword that can trigger an expensive multi-agent
// fan-out. But an operator who wants workflow mode as their normal way of
// working had to flip it again on every new task, which is exactly the kind
// of repeated manual step people stop doing. This preference supplies the
// starting value for tasks with no explicit choice yet; an explicit per-task
// toggle always wins and is never overwritten by a later change here.
//
// Pure client-side (localStorage), like the steer and comment-collapse prefs:
// "ultracode" is not a kato setting at all, it is a prompt keyword the
// composer prepends, so there is nothing for the backend to store.

import { createPreferenceStore } from './createPreferenceStore.js';

const STORAGE_KEY = 'kato.ultracodeDefault.v1';
// Default FALSE — workflow mode spawns many agents and costs real tokens, so
// it stays something the operator opts into rather than something they
// discover from a bill.
const DEFAULT_ULTRACODE_BY_DEFAULT = false;

const _store = createPreferenceStore({
  key: STORAGE_KEY,
  defaults: { ultracodeByDefault: DEFAULT_ULTRACODE_BY_DEFAULT },
  coerce: (parsed, defaults) => ({
    ultracodeByDefault: parsed.ultracodeByDefault === undefined
      ? defaults.ultracodeByDefault
      : !!parsed.ultracodeByDefault,
  }),
});

export function readUltracodeByDefault() {
  return _store.read().ultracodeByDefault;
}

export function writeUltracodeByDefault(next) {
  return _store.write({ ultracodeByDefault: !!next }).ultracodeByDefault;
}

export function subscribeUltracodeByDefault(fn) {
  return _store.subscribe((record) => fn(record.ultracodeByDefault));
}

// Test-only: the cache + listeners are module-level, so tests must reset
// between cases for isolation.
export function _resetUltracodeDefaultPref() {
  _store.reset();
}
