// Global single-flight lock for review-comment submissions.
//
// Without this, an operator with multiple comment forms open
// (one per file, one per line) could fire several submits in
// parallel — and kato runs review-fix runs immediately on
// top-of-thread submits, so two parallel submits = two parallel
// review-fix spawns racing for the same workspace. The local
// per-form ``busy`` flag inside ``CommentForm`` only guards
// double-clicks within ONE form; this store guards across forms.
//
// Same shape as ``toastStore.js`` — plain pub/sub, no React,
// reachable from non-component code.

let _busy = false;
const _listeners = new Set();


function _emit() {
  for (const fn of _listeners) {
    try { fn(_busy); } catch (_) { /* never let one subscriber break others */ }
  }
}


export const commentSubmitLock = {
  isBusy() { return _busy; },

  // Try to acquire the lock. Returns true on success, false if
  // someone else already holds it. Caller MUST call ``release``
  // exactly once via try/finally.
  acquire() {
    if (_busy) { return false; }
    _busy = true;
    _emit();
    return true;
  },

  release() {
    if (!_busy) { return; }
    _busy = false;
    _emit();
  },

  subscribe(fn) {
    _listeners.add(fn);
    fn(_busy);
    return () => { _listeners.delete(fn); };
  },
};
