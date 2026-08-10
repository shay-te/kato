import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  isLocalCommandScaffolding,
  stripLocalCommandEnvelope,
} from './localCommandEnvelope.js';

// The exact two turns Claude Code injected for a single `/context`, copied
// from the transcript the operator screenshotted.
const CAVEAT = '<local-command-caveat>Caveat: The messages below were generated '
  + 'by the user while running local commands. DO NOT respond to these messages '
  + 'or otherwise consider them in your response unless the user explicitly asks '
  + 'you to.</local-command-caveat>';
const INVOCATION = '<command-name>/context</command-name>\n'
  + '        <command-message>context</command-message>\n'
  + '        <command-args></command-args>';

test('the caveat turn is scaffolding', () => {
  assert.equal(isLocalCommandScaffolding(CAVEAT), true);
});

test('the command-invocation turn is scaffolding', () => {
  assert.equal(isLocalCommandScaffolding(INVOCATION), true);
});

test('both envelopes in one turn are still scaffolding', () => {
  assert.equal(isLocalCommandScaffolding(`${CAVEAT}\n${INVOCATION}`), true);
});

test('a command with arguments is still scaffolding', () => {
  const withArgs = '<command-name>/compact</command-name>'
    + '<command-message>compact</command-message>'
    + '<command-args>keep the test plan</command-args>';
  assert.equal(isLocalCommandScaffolding(withArgs), true);
});

test('an operator message is never scaffolding', () => {
  for (const text of [
    'fix the context meter',
    'why does /context disagree with the composer?',
    'compare <command-name> handling across the two paths',
  ]) {
    assert.equal(isLocalCommandScaffolding(text), false, text);
  }
});

test('scaffolding wrapped around real words keeps the turn visible', () => {
  // Never swallow an operator's actual message — one stray envelope on
  // screen is far cheaper than a lost instruction.
  const mixed = `${CAVEAT}\nplease also rebuild the bundle`;
  assert.equal(isLocalCommandScaffolding(mixed), false);
});

test('command output is content, not scaffolding', () => {
  const stdout = '<local-command-stdout>Tokens: 97.2k / 1m</local-command-stdout>';
  assert.equal(isLocalCommandScaffolding(stdout), false);
});

test('bare output tags with nothing in them are scaffolding', () => {
  assert.equal(
    isLocalCommandScaffolding('<local-command-stdout></local-command-stdout>'),
    true,
  );
});

test('a truncated envelope with text left over stays visible', () => {
  // The conservative rule, exercised at its edge: an unclosed tag drops out,
  // but `/context` survives it, so the turn still has something in it and is
  // shown. Erring toward one stray bubble beats erring toward a swallowed
  // operator instruction.
  assert.equal(isLocalCommandScaffolding('<command-name>/context'), false);
  assert.equal(stripLocalCommandEnvelope('<command-name>/context'), '/context');
});

test('a truncated envelope with nothing left over is scaffolding', () => {
  assert.equal(isLocalCommandScaffolding('<command-args>'), true);
});

test('empty text is not scaffolding', () => {
  // Callers already have a "no prompt text" path; conflating the two would
  // hide image-only turns, which carry no text but are real messages.
  for (const text of ['', '   ', null, undefined]) {
    assert.equal(isLocalCommandScaffolding(text), false);
  }
});

test('stripping leaves operator words untouched', () => {
  assert.equal(
    stripLocalCommandEnvelope(`${INVOCATION}\nrun the suite`),
    'run the suite',
  );
});

test('tag matching is case-insensitive', () => {
  assert.equal(isLocalCommandScaffolding('<COMMAND-NAME>/context</COMMAND-NAME>'), true);
});
