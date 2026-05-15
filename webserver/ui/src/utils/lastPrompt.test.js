import assert from 'node:assert/strict';
import test from 'node:test';

import { lastUserPromptText } from './lastPrompt.js';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';

function localUser(text) {
  return { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.USER, text };
}
function localSystem(text) {
  return { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM, text };
}
function serverUser(text) {
  return {
    source: ENTRY_SOURCE.SERVER,
    raw: { type: CLAUDE_EVENT.USER, message: { content: [{ type: 'text', text }] } },
  };
}
function serverAssistant() {
  return {
    source: ENTRY_SOURCE.SERVER,
    raw: { type: CLAUDE_EVENT.ASSISTANT, message: { content: [] } },
  };
}

test('returns empty string for empty / non-array input', function () {
  assert.equal(lastUserPromptText([]), '');
  assert.equal(lastUserPromptText(null), '');
  assert.equal(lastUserPromptText(undefined), '');
});

test('returns the only local user prompt', function () {
  assert.equal(lastUserPromptText([localUser('fix the bug')]), 'fix the bug');
});

test('returns the MOST RECENT user prompt, not the first', function () {
  const entries = [
    localUser('first ask'),
    serverAssistant(),
    localUser('second ask'),
    serverAssistant(),
  ];
  assert.equal(lastUserPromptText(entries), 'second ask');
});

test('reads a server user event (text blocks joined)', function () {
  const entries = [serverUser('line one\nline two')];
  assert.equal(lastUserPromptText(entries), 'line one\nline two');
});

test('skips non-user entries when scanning back', function () {
  const entries = [
    localUser('the real prompt'),
    serverAssistant(),
    localSystem('a system note'),
  ];
  assert.equal(lastUserPromptText(entries), 'the real prompt');
});

test('trims whitespace and ignores blank user entries', function () {
  const entries = [localUser('kept'), localUser('   ')];
  assert.equal(lastUserPromptText(entries), 'kept');
});

test('no user messages at all → empty string', function () {
  assert.equal(lastUserPromptText([serverAssistant(), localSystem('x')]), '');
});
