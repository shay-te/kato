// In-flight GIT ACTIONS, keyed by task, held app-globally.
//
// The busy flag used to live in ``useBusyAction``'s own React state, inside
// SessionHeader. Switching tabs unmounts that header, so the state went with
// it: the operator clicked "Merge master", moved to another tab and back, and
// the spinner was gone — with the merge still running server-side. Nothing was
// broken except the operator's ability to see it, which is the part that
// matters when the alternative is clicking a long git action a second time.
//
// Same shape and same reasoning as ``agentStatusStore``: plain pub/sub, no
// React, one value every surface reads. The action OUTLIVES the component that
// started it, so its state has to live outside that component.
//
// Keyed ``<taskId>:<action>`` so two tasks can merge at once without either
// one's spinner leaking onto the other's header.
//
// Deliberately NOT persisted. A page reload loses the browser's knowledge of a
// running action, and inventing one from storage would be worse: a stale
// "busy" that nothing will ever clear disables the buttons forever. On reload
// the truth comes back from the server's own state instead.

import { createPubSub } from './pubsub.js';

// A ceiling on how long a flag may claim to be busy.
//
// Moving the flag out of the component removed the accidental safety net it
// used to have: a hung action left a stuck flag, but a tab switch remounted
// the component and cleared it. Now the flag OUTLIVES the component, so a
// promise that never settles would disable that button for the rest of the
// session. Generous enough for a real multi-repo merge, short enough that a
// wedged action cannot cost the operator the button permanently.
const MAX_BUSY_MS = 15 * 60 * 1000;

// {key: startedAtMs}
let _busy = {};

const _pubsub = createPubSub(() => _busy);

export function gitActionKey(taskId, action) {
  const task = String(taskId || '').trim();
  const name = String(action || '').trim();
  return task && name ? `${task}:${name}` : '';
}

export const gitActionStore = {
  subscribe: _pubsub.subscribe,

  getSnapshot() { return _busy; },

  isBusy(key) {
    if (!key) { return false; }
    const startedAt = _busy[key];
    if (!startedAt) { return false; }
    return (Date.now() - startedAt) < MAX_BUSY_MS;
  },

  // No-op when nothing changes, so a re-render that recomputes the same value
  // cannot cascade into a render loop (the guard agentStatusStore needed too).
  setBusy(key, busy) {
    if (!key) { return; }
    const next = !!busy;
    if (!!_busy[key] === next) { return; }
    if (next) {
      _busy = { ..._busy, [key]: Date.now() };
    } else {
      const copy = { ..._busy };
      delete copy[key];
      _busy = copy;
    }
    _pubsub.emit();
  },

  // Every in-flight action for a task — lets a surface ask "is this task
  // mid-git-op?" without knowing which action it is.
  isTaskBusy(taskId) {
    const prefix = `${String(taskId || '').trim()}:`;
    if (prefix === ':') { return false; }
    return Object.keys(_busy).some(
      (key) => key.startsWith(prefix) && gitActionStore.isBusy(key),
    );
  },

  // Test seam: drop everything.
  _reset() { _busy = {}; _pubsub.emit(); },
};
