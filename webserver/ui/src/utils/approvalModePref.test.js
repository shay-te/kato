// The approval-mode preference.
//
// Two genuinely different answers to "where does an approval request appear",
// neither right for everyone: an interrupting dialog costs you your place in
// another task, and a quiet in-chat card costs a blocked agent some of your
// attention. Stored client-side like the other UI prefs.

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  APPROVAL_MODE_GLOBAL,
  APPROVAL_MODE_IN_CHAT,
  readApprovalMode,
  subscribeApprovalMode,
  writeApprovalMode,
  _resetApprovalModePref,
} from './approvalModePref.js';

function reset() {
  globalThis.localStorage?.removeItem?.('kato.approvalMode.v1');
  _resetApprovalModePref();
}

test('it defaults to the in-chat card', () => {
  reset();
  assert.equal(readApprovalMode(), APPROVAL_MODE_IN_CHAT);
});

test('it round-trips the global window', () => {
  reset();
  writeApprovalMode(APPROVAL_MODE_GLOBAL);
  assert.equal(readApprovalMode(), APPROVAL_MODE_GLOBAL);
});

test('it round-trips back to in-chat', () => {
  reset();
  writeApprovalMode(APPROVAL_MODE_GLOBAL);
  writeApprovalMode(APPROVAL_MODE_IN_CHAT);
  assert.equal(readApprovalMode(), APPROVAL_MODE_IN_CHAT);
});

test('an unrecognised value falls back to the default', () => {
  // A corrupt value must never leave the operator with an ask that renders
  // nowhere — the one outcome worse than either mode.
  reset();
  assert.equal(writeApprovalMode('nonsense'), APPROVAL_MODE_IN_CHAT);
  assert.equal(readApprovalMode(), APPROVAL_MODE_IN_CHAT);
  reset();
  assert.equal(writeApprovalMode(undefined), APPROVAL_MODE_IN_CHAT);
});

test('subscribers hear a change', () => {
  reset();
  const seen = [];
  const stop = subscribeApprovalMode((mode) => seen.push(mode));
  writeApprovalMode(APPROVAL_MODE_GLOBAL);
  stop();
  writeApprovalMode(APPROVAL_MODE_IN_CHAT);
  assert.deepEqual(seen, [APPROVAL_MODE_GLOBAL]);
});

test('a corrupt stored record reads as the default', () => {
  reset();
  globalThis.localStorage?.setItem?.('kato.approvalMode.v1', '{not json');
  _resetApprovalModePref();
  assert.equal(readApprovalMode(), APPROVAL_MODE_IN_CHAT);
});
