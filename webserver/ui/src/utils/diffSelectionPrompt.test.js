// Locks the chat-composer wording produced by the diff-pane right-click
// "Place in chat". The fragment is what Claude reads as the operator's
// prompt, so a reword can change agent behaviour — treat changes here as
// content review, not string nits.

import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildChatFragmentFromSelection,
  formatSelectionReference,
} from './diffSelectionPrompt.js';


describe('formatSelectionReference', () => {
  test('a multi-line selection becomes a compact L<start>-L<end> reference', () => {
    // The whole point: NAME the lines, never paste the code — so the
    // composer stays readable and Claude reads the file itself.
    assert.equal(
      formatSelectionReference('src/auth.py', 'admin-backend', { start: 20, end: 45 }),
      '`admin-backend:src/auth.py:L20-L45`',
    );
  });

  test('a single-line selection collapses to L<n>', () => {
    assert.equal(
      formatSelectionReference('src/auth.py', 'r', { start: 12, end: 12 }),
      '`r:src/auth.py:L12`',
    );
  });

  test('no line range → bare file reference (freeform ask about the file)', () => {
    assert.equal(
      formatSelectionReference('src/auth.py', 'admin-backend', null),
      '`admin-backend:src/auth.py`',
    );
  });

  test('omits the repo prefix when no repo id is supplied', () => {
    assert.equal(
      formatSelectionReference('src/auth.py', '', { start: 3, end: 8 }),
      '`src/auth.py:L3-L8`',
    );
  });

  test('returns empty string when path is missing', () => {
    assert.equal(formatSelectionReference('', 'r', { start: 1, end: 2 }), '');
    assert.equal(formatSelectionReference(null), '');
  });

  test('a non-finite range degrades to the bare file reference', () => {
    assert.equal(
      formatSelectionReference('a.js', 'r', { start: NaN, end: 5 }),
      '`r:a.js`',
    );
  });
});


describe('buildChatFragmentFromSelection', () => {
  test('with no DOM selection (SSR / tests) it is the bare file reference — never a code dump', () => {
    // No global.window → selectedNewLineRange() returns null.
    assert.equal(
      buildChatFragmentFromSelection('src/auth.py', 'admin-backend'),
      '`admin-backend:src/auth.py`',
    );
    // And crucially it never emits a fenced code block.
    assert.ok(!buildChatFragmentFromSelection('src/auth.py', 'r').includes('```'));
  });

  test('returns empty string when path is missing', () => {
    assert.equal(buildChatFragmentFromSelection(''), '');
    assert.equal(buildChatFragmentFromSelection(null), '');
  });
});
