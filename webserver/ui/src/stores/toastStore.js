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

let _toasts = [];
const _listeners = new Set();
let _nextId = 1;

function _emit() {
  // Snapshot copy so subscribers can't accidentally mutate state.
  const snapshot = _toasts.slice();
  for (const fn of _listeners) {
    try { fn(snapshot); } catch (_) { /* never let one subscriber break others */ }
  }
}

export const toastStore = {
  subscribe(fn) {
    _listeners.add(fn);
    // Fire once immediately so late mounters render the current state.
    // Wrapped in try/catch so a throwing subscriber can't take down
    // the caller of subscribe() — same defense as _emit().
    try { fn(_toasts.slice()); } catch (_) { /* see _emit */ }
    return () => _listeners.delete(fn);
  },

  push({
    kind = 'info',
    title = '',
    message = '',
    durationMs = 5000,
  } = {}) {
    const id = _nextId++;
    _toasts = [..._toasts, { id, kind, title, message, createdAt: Date.now() }];
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
  dismiss: (id) => toastStore.dismiss(id),
  clear:   () => toastStore.clear(),
};
