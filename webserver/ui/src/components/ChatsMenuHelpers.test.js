import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { chatMeta, chatTitle } from './ChatsMenuHelpers.js';

test('chatTitle uses the first user message as the menu name', () => {
  assert.equal(
    chatTitle({ first_user_message: '  Fix payment tests  ' }),
    'Fix payment tests',
  );
});

test('chatTitle falls back to a short session id label', () => {
  assert.equal(
    chatTitle({ [AGENT_SESSION_ID]: 'abcdef1234567890' }),
    'Chat abcdef12...',
  );
});

test('chatMeta marks the active chat as current', () => {
  assert.equal(chatMeta({ active: true, turn_count: 8 }), 'current');
});

test('chatMeta formats detached chat turn counts', () => {
  assert.equal(chatMeta({ turn_count: 1 }), '1 turn');
  assert.equal(chatMeta({ turn_count: 3 }), '3 turns');
});
