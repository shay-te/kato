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

const STORAGE_KEY = 'kato.composerSteer.v1';
// Default TRUE: preserve the existing hold-until-idle behavior for everyone
// who doesn't opt into immediate send, so this change is invisible until an
// operator flips it.
const DEFAULT_STEER_WHILE_WORKING = true;

let _cache = null;
const _listeners = new Set();

export function readSteerWhileWorking() {
  if (_cache !== null) { return _cache; }
  try {
    const raw = typeof localStorage !== 'undefined'
      ? localStorage.getItem(STORAGE_KEY) : null;
    const parsed = raw ? JSON.parse(raw) : {};
    _cache = parsed.steerWhileWorking === undefined
      ? DEFAULT_STEER_WHILE_WORKING
      : !!parsed.steerWhileWorking;
  } catch (_) {
    _cache = DEFAULT_STEER_WHILE_WORKING;
  }
  return _cache;
}

export function writeSteerWhileWorking(next) {
  _cache = !!next;
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(
        STORAGE_KEY, JSON.stringify({ steerWhileWorking: _cache }),
      );
    }
  } catch (_) { /* private mode / quota — keep the in-memory value */ }
  for (const fn of _listeners) {
    try { fn(_cache); } catch (_) { /* isolate a throwing subscriber */ }
  }
  return _cache;
}

export function subscribeSteerWhileWorking(fn) {
  _listeners.add(fn);
  return () => { _listeners.delete(fn); };
}

// Test-only: the cache + listeners are module-level, so tests must reset
// between cases for isolation.
export function _resetSteerWhileWorkingPref() {
  _cache = null;
  _listeners.clear();
}
