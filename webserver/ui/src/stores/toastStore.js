// Tiny standalone store for transient top-of-screen notifications.
//
// Anywhere in the app:
//
//     import { toast } from '../stores/toastStore.js';
//     toast.success('Done — task finalised.');
//     toast.error('Something exploded:\n' + err);
//
// The container component (mounted once at App level) subscribes to
// the store and renders the visible stack. Toasts auto-dismiss after
// `durationMs` (default 5000) and can also be clicked to dismiss
// early. No React, no context — plain pub/sub so non-component code
// (api error handlers, hooks) can fire toasts too.

import { createPubSub } from './pubsub.js';
import { apiErrorMessage } from '../utils/apiError.js';

let _toasts = [];
let _nextId = 1;

// Snapshot copy so subscribers can't accidentally mutate state.
const _pubsub = createPubSub(() => _toasts.slice());
const _emit = _pubsub.emit;

export const toastStore = {
  subscribe: _pubsub.subscribe,

  push({
    kind = 'info',
    title = '',
    message = '',
    durationMs = 5000,
    // Optional task identity for GLOBAL toasts that originate from a
    // task-specific action (sync repositories, push, finish, etc.).
    // When set, ToastContainer renders a "<id> — <summary>" chip
    // above the title so the operator can tell at a glance which
    // task the popup belongs to. Free-floating toasts (settings,
    // network errors) leave both fields blank and render the same
    // way as before.
    taskId = '',
    taskSummary = '',
  } = {}) {
    const id = _nextId++;
    _toasts = [
      ..._toasts,
      {
        id, kind, title, message, taskId, taskSummary,
        // Carried on the entry so the card can tell a STICKY toast from a
        // timed one. A sticky toast is one the operator is meant to read,
        // so a stray click — selecting a repo name out of the report, for
        // instance — must not destroy it.
        durationMs,
        createdAt: Date.now(),
      },
    ];
    _emit();
    if (durationMs > 0) {
      setTimeout(() => toastStore.dismiss(id), durationMs);
    }
    return id;
  },

  dismiss(id) {
    const before = _toasts.length;
    _toasts = _toasts.filter((t) => t.id !== id);
    if (_toasts.length !== before) { _emit(); }
  },

  clear() {
    if (_toasts.length === 0) { return; }
    _toasts = [];
    _emit();
  },
};

// Convenience helpers — `toast.success("hi")` is the common case;
// callers that need title + custom duration use `toast.show({...})`.
export const toast = {
  info:    (message, opts = {}) => toastStore.push({ ...opts, kind: 'info',    message }),
  success: (message, opts = {}) => toastStore.push({ ...opts, kind: 'success', message }),
  warning: (message, opts = {}) => toastStore.push({ ...opts, kind: 'warning', message }),
  error:   (message, opts = {}) => toastStore.push({ ...opts, kind: 'error',   message }),
  show:    (opts) => toastStore.push(opts),
  // Surface a failed ``{ ok, body, error }`` API result as an error
  // toast. Collapses the "build the error envelope, run the message
  // through apiErrorMessage" idiom that was copy-pasted across ~16
  // call sites (modals, diff/editor comment handlers, settings
  // panels, the session header). The ``message`` precedence is the
  // canonical one (body.error → result.error → fallback) so every
  // caller agrees on which text wins. ``durationMs`` defaults to
  // 8000 but each site can override to keep its existing duration.
  errorFromResult: (result, { title, fallback = '', durationMs = 8000 } = {}) =>
    toastStore.push({
      kind: 'error',
      title,
      message: apiErrorMessage(result, fallback),
      durationMs,
    }),
  dismiss: (id) => toastStore.dismiss(id),
  clear:   () => toastStore.clear(),
};

// Dispatch a pre-built ``{ kind, title, message }`` result object as a
// toast, applying the one duration rule every git-action handler (Merge /
// Pull / Push / Finish / Update source / Sync) shares. The formatXResult
// builders stay distinct per payload; only this trailing dispatch is shared.
//
// AN ACTION THAT DID NOT FULLY COMPLETE NEVER EXPIRES.
//
// A blocked or failed repo is something the operator has to act on, and a
// timer takes it away mid-read — or before it is read at all. One operator
// clicked "Merge master" FIVE times before realising a repo was refusing:
// the report was amber, up for six seconds, with the one ⚠ line buried
// under twenty-four green ones. Losing a report you must act on is strictly
// worse than a card that waits for a click, so problems now stay put.
//
// ``problemMs`` remains overridable, but the default is the policy: 0.
export function toastResult(
  { kind = 'info', title, message, taskId = '', taskSummary = '' } = {},
  { problemMs = 0, defaultMs = 7000 } = {},
) {
  // Both kinds, not just ``error``: a partial run is reported as a warning
  // and is exactly the case that was being missed.
  const needsAttention = kind === 'error' || kind === 'warning';
  return toastStore.push({
    kind,
    title,
    message,
    taskId,
    taskSummary,
    durationMs: needsAttention ? problemMs : defaultMs,
  });
}
