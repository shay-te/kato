// Adversarial regression tests for ``useSessionStream`` reducer bugs
// surfaced by the deep audit:
//
// Bug A: ``ACTION_LIFECYCLE`` transitioning to CLOSED / IDLE / MISSING
//        must reset ``turnInFlight`` to false. Otherwise the
//        WorkingIndicator stays "Claude is thinking…" forever on a
//        subprocess that has actually died, with no way for the
//        operator to recover except a full tab restart.
//
// Bug B: On reconnect (tab remount, SSE re-open), the hook dispatches
//        ``ACTION_HYDRATE`` with the cached state — but currently
//        forces ``lifecycle: CONNECTING`` regardless of what the
//        cache said. If the cache was STREAMING (subprocess alive,
//        mid-turn) the user sees a spurious "Connecting…" banner
//        before any new events arrive. Status should reflect what's
//        actually true; a remount during a live stream should remain
//        STREAMING until proven otherwise.

import assert from 'node:assert/strict';
import test from 'node:test';

import { SESSION_LIFECYCLE, reducer } from './useSessionStream.js';


// ---------------------------------------------------------------------------
// Bug A: turnInFlight stuck on CLOSED / IDLE / MISSING
// ---------------------------------------------------------------------------

function _midTurnState() {
  return {
    events: [],
    lifecycle: SESSION_LIFECYCLE.STREAMING,
    turnInFlight: true,
    pendingPermission: null,
    lastEventAt: Date.now(),
    streamGeneration: 0,
  };
}

test('Bug A: turnInFlight resets to false when lifecycle goes CLOSED', function () {
  const state = _midTurnState();
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.CLOSED });
  assert.equal(
    next.turnInFlight, false,
    'WorkingIndicator will stay "Claude is thinking…" on a dead subprocess',
  );
});

test('Bug A: turnInFlight resets to false when lifecycle goes IDLE', function () {
  // Subprocess exited cleanly without a final RESULT event (e.g., timeout
  // from kato's side). The UI must transition out of the "working" state.
  const state = _midTurnState();
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.IDLE });
  assert.equal(next.turnInFlight, false);
});

test('Bug A: turnInFlight resets to false when lifecycle goes MISSING', function () {
  // Record disappeared from the server (very rare — manual cleanup,
  // disk wipe). UI must not pretend the tab is still working.
  const state = _midTurnState();
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.MISSING });
  assert.equal(next.turnInFlight, false);
});

test('Bug A: pendingPermission also clears on CLOSED (existing contract)', function () {
  // Don't regress the existing behavior in fixing turnInFlight: the
  // permission modal must still vanish when the session closes.
  const state = { ..._midTurnState(), pendingPermission: { request_id: 'r1' } };
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.CLOSED });
  assert.equal(next.pendingPermission, null);
});

test('Bug A: lifecycle CONNECTING does NOT touch turnInFlight (reconnect mid-turn)', function () {
  // Negative: only terminal lifecycle states should reset turnInFlight.
  // A reconnect during a turn must NOT pretend the turn ended.
  const state = _midTurnState();
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.CONNECTING });
  assert.equal(next.turnInFlight, true);
});

test('Bug A: lifecycle STREAMING does NOT touch turnInFlight', function () {
  // Negative: STREAMING preserves whatever turnInFlight was.
  const state = { ..._midTurnState(), turnInFlight: false };
  const next = reducer(state, { type: 'lifecycle', value: SESSION_LIFECYCLE.STREAMING });
  assert.equal(next.turnInFlight, false);
});


// ---------------------------------------------------------------------------
// Bug B: HYDRATE with cached STREAMING shouldn't be silently overridden
// to CONNECTING. The hook currently does:
//   dispatch({ type: HYDRATE, value: { ...cached, lifecycle: CONNECTING }})
// which means even a cache that says "the session is alive mid-stream"
// becomes "Connecting…" on every remount. This test pins the reducer
// contract: HYDRATE preserves whatever value is passed. The hook fix
// (in useSessionStream's useEffect) will pass the cached lifecycle
// when it's STREAMING.
// ---------------------------------------------------------------------------

test('Bug B: HYDRATE preserves the lifecycle value it is given', function () {
  // The reducer itself is correct — it simply replaces state with the
  // hydrated value. The bug is in the *caller* (useEffect). This test
  // documents the reducer contract: pass in what you want, get it back.
  const cached = _midTurnState();
  const next = reducer({}, { type: 'hydrate', value: cached });
  assert.equal(next.lifecycle, SESSION_LIFECYCLE.STREAMING);
  assert.equal(next.turnInFlight, true);
});
