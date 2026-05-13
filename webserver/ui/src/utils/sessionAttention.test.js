// Tests for ``mergePendingPermissionTaskIds`` — controls which tabs
// get the orange attention dot when sessions are awaiting a tool
// permission decision. Contract:
//   - MERGES additions onto the existing set (does NOT remove).
//   - Suppresses tasks whose pending tool already has a remembered
//     "allow" / "deny" decision (those auto-handle silently).
//   - Falls back to legacy behaviour when no recall function is
//     given.

import assert from 'node:assert/strict';
import test from 'node:test';

import { mergePendingPermissionTaskIds } from './sessionAttention.js';


function _session(overrides = {}) {
  return {
    task_id: 'T1',
    has_pending_permission: true,
    pending_permission_tool_name: 'Bash',
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
// Recall-decision suppression (tool-memory integration)
// ---------------------------------------------------------------------------

test('merge: suppresses when recall returns "allow" (auto-handled tool)', function () {
  // The PermissionDecisionContainer auto-allows without a modal.
  // Showing the orange dot would mislead the operator into thinking
  // they need to click something.
  const out = mergePendingPermissionTaskIds(
    new Set(),
    [_session({ task_id: 'T1', pending_permission_tool_name: 'Bash' })],
    (tool) => (tool === 'Bash' ? 'allow' : null),
  );
  assert.equal(out.size, 0);
});

test('merge: suppresses when recall returns "deny"', function () {
  const out = mergePendingPermissionTaskIds(
    new Set(),
    [_session({ task_id: 'T1', pending_permission_tool_name: 'Bash' })],
    () => 'deny',
  );
  assert.equal(out.size, 0);
});

test('merge: marks attention when recall returns null (no remembered decision)', function () {
  // Operator hasn't decided yet → modal will render → tab needs
  // attention.
  const out = mergePendingPermissionTaskIds(
    new Set(),
    [_session({ task_id: 'T1', pending_permission_tool_name: 'Bash' })],
    () => null,
  );
  assert.ok(out.has('T1'));
});

test('merge: marks attention when tool name is missing (defensive)', function () {
  // Tool name empty → cannot look up a decision → conservative:
  // mark attention so the modal renders.
  const out = mergePendingPermissionTaskIds(
    new Set(),
    [_session({ task_id: 'T1', pending_permission_tool_name: '' })],
    () => 'allow',  // would suppress IF tool name were set
  );
  assert.ok(out.has('T1'));
});

test('merge: marks attention when recall is not a function', function () {
  // recallToolDecision can be null, undefined, an object — only
  // a callable triggers the suppression path. Anything else falls
  // back to "mark attention".
  for (const recall of [null, undefined, {}, 'allow', 42]) {
    const out = mergePendingPermissionTaskIds(
      new Set(),
      [_session({ task_id: 'T1', pending_permission_tool_name: 'Bash' })],
      recall,
    );
    assert.ok(
      out.has('T1'),
      `non-function recall (${typeof recall}) should not suppress attention`,
    );
  }
});

test('merge: arbitrary truthy recall values do NOT suppress (only "allow"/"deny")', function () {
  // Defensive against a future recall that returns e.g. true.
  // The check requires the literal strings 'allow' or 'deny'.
  for (const decision of ['yes', true, 1, 'maybe', 'ALLOW' /* wrong case */]) {
    const out = mergePendingPermissionTaskIds(
      new Set(),
      [_session({ task_id: 'T1', pending_permission_tool_name: 'Bash' })],
      () => decision,
    );
    assert.ok(
      out.has('T1'),
      `non-literal recall return (${decision}) should not suppress attention`,
    );
  }
});

test('merge: tool name is trimmed before lookup', function () {
  // Sessions may surface whitespace-padded tool names from the
  // backend. The function trims before checking truthiness.
  let observedTool = null;
  const out = mergePendingPermissionTaskIds(
    new Set(),
    [_session({ task_id: 'T1', pending_permission_tool_name: '  Bash  ' })],
    (tool) => {
      observedTool = tool;
      return 'allow';
    },
  );
  assert.equal(observedTool, 'Bash');
  assert.equal(out.size, 0);  // suppressed
});

test('merge: whitespace-only tool name falls through to "mark attention"', function () {
  // `"   ".trim()` is "", so the tool-name truthy check fails,
  // skipping recall and going straight to mark.
  const out = mergePendingPermissionTaskIds(
    new Set(),
    [_session({ task_id: 'T1', pending_permission_tool_name: '   ' })],
    () => 'allow',
  );
  assert.ok(out.has('T1'));
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

test('merge: only auto-handled tasks are suppressed; others still mark', function () {
  const out = mergePendingPermissionTaskIds(
    new Set(),
    [
      _session({ task_id: 'T1', pending_permission_tool_name: 'Bash' }),  // suppressed
      _session({ task_id: 'T2', pending_permission_tool_name: 'Write' }),  // marked
      _session({ task_id: 'T3', pending_permission_tool_name: '' }),  // marked (no tool name)
    ],
    (tool) => (tool === 'Bash' ? 'allow' : null),
  );
  assert.ok(!out.has('T1'));
  assert.ok(out.has('T2'));
  assert.ok(out.has('T3'));
});
