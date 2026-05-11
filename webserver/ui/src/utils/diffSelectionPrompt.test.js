// Locks the chat-composer wording produced by the Changes-tab
// right-click handler. The fragment is what Claude reads as the
// operator's prompt, so a reword can change agent behaviour —
// treat changes here as content review, not string nits.

import { describe, afterEach, test } from 'node:test';
import assert from 'node:assert/strict';

import { buildChatFragmentFromSelection } from './diffSelectionPrompt.js';


function installSelection(text) {
  global.window = {
    getSelection: () => ({
      toString: () => text,
    }),
  };
}


function uninstallSelection() {
  delete global.window;
}


describe('buildChatFragmentFromSelection', () => {
  afterEach(uninstallSelection);

  test('with no selection, falls back to a bare file reference', () => {
    installSelection('');
    const fragment = buildChatFragmentFromSelection('src/auth.py', 'admin-backend');
    // Bare backtick-wrapped path so the operator's freeform prompt
    // ("please refactor this") still reads as natural prose with
    // an inline code reference.
    assert.equal(fragment, '`admin-backend:src/auth.py`');
  });

  test('with a selection, wraps the text in a fenced block under the file header', () => {
    installSelection('+ const x = 1\n- const x = 2');
    const fragment = buildChatFragmentFromSelection(
      'src/auth.py', 'admin-backend',
    );
    // ``In `repo:path` the following diff lines:`` is the load-
    // bearing wording — Claude treats this as "the operator
    // wants me to act on these specific lines" rather than
    // "here's some context, decide what to do."
    assert.match(fragment, /^In `admin-backend:src\/auth\.py` the following diff lines:\n/);
    assert.match(fragment, /```\n\+ const x = 1\n- const x = 2\n```$/);
  });

  test('omits the repo prefix when no repo id is supplied', () => {
    installSelection('');
    assert.equal(
      buildChatFragmentFromSelection('src/auth.py'),
      '`src/auth.py`',
    );
  });

  test('truncates pathologically long selections so the composer doesn\'t blow up', () => {
    const longText = 'x'.repeat(20 * 1024);
    installSelection(longText);
    const fragment = buildChatFragmentFromSelection('big.py', 'r');
    assert.match(fragment, /\(selection truncated\)\n```$/);
    assert.ok(fragment.length < 9 * 1024);
  });

  test('returns empty string when path is missing', () => {
    installSelection('whatever');
    assert.equal(buildChatFragmentFromSelection(''), '');
    assert.equal(buildChatFragmentFromSelection(null), '');
  });

  test('handles missing window.getSelection gracefully (SSR / tests)', () => {
    // No installSelection() — global.window is undefined.
    assert.equal(
      buildChatFragmentFromSelection('src/auth.py', 'r'),
      '`r:src/auth.py`',
    );
  });
});
