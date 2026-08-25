// A Codex turn must END, so the "working" chip and the spinner clear.
//
// Reported twice: the indicator span forever ("last activity 1m31s ago" under
// a completed turn), and the status chip only flipped to idle after switching
// to the Claude tab and back — a remount re-deriving what the stream should
// have told it. The reducer only knew Claude's ``result``, so a Codex turn
// set "working" and nothing ever cleared it.

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  CODEX_EVENT, isCodexTerminal, isCodexHidden, codexAgentMessage,
} from '../constants/codexEvent.js';

test('every turn-ending event is terminal', () => {
  for (const type of ['turn.completed', 'turn.failed', 'turn.aborted']) {
    assert.equal(isCodexTerminal(type), true, type);
  }
});

test('mid-turn events are NOT terminal', () => {
  for (const type of ['thread.started', 'turn.started', 'item.completed', '']) {
    assert.equal(isCodexTerminal(type), false, type);
  }
});

test('lifecycle noise is hidden, results are not', () => {
  for (const type of ['thread.started', 'turn.started', 'item.started',
    'item.updated']) {
    assert.equal(isCodexHidden(type), true, type);
  }
  for (const type of ['item.completed', 'turn.completed', 'turn.failed',
    'error']) {
    assert.equal(isCodexHidden(type), false, type);
  }
});

test('the agent message is pulled out of a completed item', () => {
  assert.equal(
    codexAgentMessage({
      type: 'item.completed',
      item: { type: 'agent_message', text: '  Hello back!  ' },
    }),
    'Hello back!',
  );
});

test('non-message items yield no text', () => {
  assert.equal(codexAgentMessage({
    type: 'item.completed',
    item: { type: 'command_execution', command: 'ls' },
  }), '');
  assert.equal(codexAgentMessage({ type: 'turn.completed' }), '');
  assert.equal(codexAgentMessage(null), '');
});

test('the constants match the CLI wire protocol', () => {
  // Pinned because these strings come from `codex exec --json` output, not
  // from anything we control — a typo here silently disables the handling.
  assert.equal(CODEX_EVENT.TURN_COMPLETED, 'turn.completed');
  assert.equal(CODEX_EVENT.ITEM_COMPLETED, 'item.completed');
  assert.equal(CODEX_EVENT.THREAD_STARTED, 'thread.started');
});
