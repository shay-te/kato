// Tests for ``mergePendingPermissionTaskIds`` — controls which tabs
// get the orange attention dot when sessions are awaiting a tool
// permission decision. Contract:
//   - MERGES additions onto the existing set (does NOT remove).
//   - Marks every session with ``has_pending_permission`` — the
//     backend already auto-resolves anything a remembered decision
//     covers before reporting that flag (see
//     kato_core_lib/helpers/tool_decision_store.py and
//     _pending_permission_tool_by_task in kato_webserver/app.py), so
//     anything reaching here genuinely needs the operator.

import assert from 'node:assert/strict';
import test from 'node:test';

import { mergePendingPermissionTaskIds } from './sessionAttention.js';


function _session(overrides = {}) {
  return {
    task_id: 'T1',
    has_pending_permission: true,
    ...overrides,
  };
}


// ---------------------------------------------------------------------------
// Defensive / empty inputs
// ---------------------------------------------------------------------------

test('merge: returns a Set even for empty inputs', function () {
  const out = mergePendingPermissionTaskIds(new Set(), []);
  assert.ok(out instanceof Set);
  assert.equal(out.size, 0);
});

test('merge: tolerates null sessions list', function () {
  const out = mergePendingPermissionTaskIds(new Set(['existing']), null);
  assert.deepEqual(Array.from(out), ['existing']);
});

test('merge: tolerates null entries in sessions list', function () {
  const out = mergePendingPermissionTaskIds(
    new Set(), [null, undefined, _session({ task_id: 'T1' })],
  );
  assert.deepEqual(Array.from(out), ['T1']);
});

test('merge: skips sessions without task_id', function () {
  // Can't address an attention-mark to a task without an id.
  const out = mergePendingPermissionTaskIds(new Set(), [
    _session({ task_id: '' }),
    _session({ task_id: null }),
  ]);
  assert.equal(out.size, 0);
});

test('merge: skips sessions where has_pending_permission is false', function () {
  // No pending permission → not eligible for attention regardless
  // of other fields.
  const out = mergePendingPermissionTaskIds(new Set(), [
    _session({ has_pending_permission: false }),
    _session({ task_id: 'T2', has_pending_permission: 0 }),
  ]);
  assert.equal(out.size, 0);
});


// ---------------------------------------------------------------------------
// Core merge behaviour
// ---------------------------------------------------------------------------

test('merge: preserves existing task ids (merge, not replace)', function () {
  // The function is called every poll cycle. Pre-existing attention
  // ids (from prior cycles or other code paths) must survive.
  const initial = new Set(['existing-1', 'existing-2']);
  const out = mergePendingPermissionTaskIds(
    initial, [_session({ task_id: 'new-1' })],
  );
  assert.deepEqual(
    Array.from(out).sort(),
    ['existing-1', 'existing-2', 'new-1'],
  );
});

test('merge: deduplicates when a session id is already in the input set', function () {
  // A task already marked should not be double-added.
  const out = mergePendingPermissionTaskIds(
    new Set(['T1']), [_session({ task_id: 'T1' })],
  );
  assert.equal(out.size, 1);
  assert.ok(out.has('T1'));
});

test('merge: input set is NOT mutated (returns a fresh Set)', function () {
  // Mutating the caller's set would surprise React state-update
  // semantics elsewhere. Result must be a new Set instance.
  const initial = new Set(['existing']);
  const out = mergePendingPermissionTaskIds(
    initial, [_session({ task_id: 'T1' })],
  );
  assert.notEqual(out, initial);
  assert.equal(initial.size, 1, 'caller set was mutated — unexpected side effect');
  assert.equal(out.size, 2);
});


// ---------------------------------------------------------------------------
// Multiple sessions
// ---------------------------------------------------------------------------

test('merge: marks every eligible session in one pass', function () {
  const out = mergePendingPermissionTaskIds(
    new Set(),
    [
      _session({ task_id: 'T1' }),
      _session({ task_id: 'T2' }),
      _session({ task_id: 'T3' }),
    ],
  );
  assert.equal(out.size, 3);
});

test('merge: a non-list third argument is ignored, not thrown on', function () {
  // The third parameter USED to be unused. It now carries pending-permission
  // task ids, but this feeds the tab strip — anything unexpected has to be a
  // no-op rather than take the whole strip down.
  const out = mergePendingPermissionTaskIds(
    new Set(),
    [_session({ task_id: 'T1' })],
    () => 'allow',
  );
  assert.ok(out.has('T1'));
});

test('merge: pending-permission task ids light the badge', function () {
  // The dialog only opens for the task on screen now, so this badge is how a
  // BACKGROUND task says it is waiting for an answer.
  const out = mergePendingPermissionTaskIds(new Set(), [], ['T9']);
  assert.ok(out.has('T9'));
});

test('merge: the store is folded in ON TOP of the sessions poll', function () {
  // Both describe the same server truth, but the store hears first — it has
  // its own poll plus the live SSE push, while ``has_pending_permission``
  // only refreshes on the slower sessions tick.
  const out = mergePendingPermissionTaskIds(
    new Set(['T1']),
    [_session({ task_id: 'T2', has_pending_permission: true })],
    ['T3'],
  );
  assert.deepEqual([...out].sort(), ['T1', 'T2', 'T3']);
});

test('merge: empty ids from the store are skipped', function () {
  const out = mergePendingPermissionTaskIds(new Set(), [], ['', null, 'T4']);
  assert.deepEqual([...out], ['T4']);
});
