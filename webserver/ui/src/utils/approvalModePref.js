// Operator preference: WHERE an approval request appears.
//
//   * approvalMode = 'in-chat' (default) — the ask renders inside the chat of
//     the task that raised it, between the transcript and the composer. A
//     task you are not looking at does not interrupt you; it lights its tab
//     and appears in the header's "waiting for you" roster, and the ask is
//     there when you switch to it.
//
//   * approvalMode = 'global' — a modal over the whole app for ANY task,
//     wherever you are. kato's original behaviour. It interrupts, which is
//     the point: an agent stays blocked until the ask is answered, and some
//     operators would rather be pulled out of what they are doing than risk
//     a background task sitting idle.
//
// Neither is right for everyone, which is why it is a setting rather than a
// default someone has to live with. The ask, the submit path and the audit
// bubble are identical either way — only where it is drawn changes.
//
// A pure client-side UI preference (like the composer-steer / permission-sound
// prefs), backed by localStorage so it survives reloads. It never touches the
// backend: the permission store still watches EVERY task in both modes, so no
// ask can be missed because of how it is displayed.

import { createPreferenceStore } from './createPreferenceStore.js';

const STORAGE_KEY = 'kato.approvalMode.v1';

export const APPROVAL_MODE_IN_CHAT = 'in-chat';
export const APPROVAL_MODE_GLOBAL = 'global';

// Default IN-CHAT: a modal for a task you are not on interrupts whatever you
// were doing, which is what prompted this setting to exist. An operator who
// wants the interruption can ask for it.
const DEFAULT_APPROVAL_MODE = APPROVAL_MODE_IN_CHAT;

const _store = createPreferenceStore({
  key: STORAGE_KEY,
  defaults: { approvalMode: DEFAULT_APPROVAL_MODE },
  coerce: (parsed, defaults) => ({
    // Anything unrecognised falls back to the default rather than being
    // stored as-is: a corrupt value must not leave the operator with an ask
    // that renders nowhere.
    approvalMode: parsed.approvalMode === APPROVAL_MODE_GLOBAL
      ? APPROVAL_MODE_GLOBAL
      : defaults.approvalMode,
  }),
});

export function readApprovalMode() {
  return _store.read().approvalMode;
}

export function writeApprovalMode(next) {
  return _store.write({
    approvalMode: next === APPROVAL_MODE_GLOBAL
      ? APPROVAL_MODE_GLOBAL
      : APPROVAL_MODE_IN_CHAT,
  }).approvalMode;
}

export function subscribeApprovalMode(fn) {
  return _store.subscribe((record) => fn(record.approvalMode));
}

// Test-only: the cache + listeners are module-level, so tests must reset
// between cases for isolation.
export function _resetApprovalModePref() {
  _store.reset();
}
