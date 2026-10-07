// The loop's findings message must read as kato's in the chat, never as
// "You asked" — including when kato's workspace preamble sits above it.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { parseReviewLoopPrompt, stickyPromptLabel } from './reviewLoopPrompt.js';

const FINDINGS = 'Kato review loop — round 2 of 5\n\nAn independent reviewer read the whole change…';

test('a findings message is recognised by its header', () => {
  assert.deepEqual(parseReviewLoopPrompt(FINDINGS), { round: 2, maxRounds: 5 });
  assert.equal(stickyPromptLabel(FINDINGS), 'Kato · review loop round 2/5');
});

test('the header is found below kato\'s respawn preamble too', () => {
  const respawned = `WORKSPACE SCOPE — STRICT BOUNDARY (read this first):\n…\n\n${FINDINGS}`;
  assert.deepEqual(parseReviewLoopPrompt(respawned), { round: 2, maxRounds: 5 });
});

test('anything else is what the operator asked', () => {
  for (const text of ['please fix the login', 'Kato review loop — round two of five', '', null,
    'I read "Kato review loop — round 2 of 5" in the docs']) {
    assert.equal(parseReviewLoopPrompt(text), null);
    assert.equal(stickyPromptLabel(text), 'You asked');
  }
});

test('the self-check and test messages are kato\'s too', () => {
  const cases = [
    ['Kato review loop — self-check 1 of 3\n\nBefore an independent reviewer…', 'self-check 1 of 3'],
    ['WORKSPACE SCOPE …\n\nKato review loop — run the tests\n\nThe independent review…', 'run the tests'],
    ['Kato review loop — fix the failing tests\n\nThe task\'s tests are failing:', 'fix the failing tests'],
  ];
  for (const [text, stage] of cases) {
    assert.deepEqual(parseReviewLoopPrompt(text), { stage });
    assert.equal(stickyPromptLabel(text), `Kato · review loop · ${stage}`);
  }
  // An operator who TYPES the words mid-sentence is still "You asked".
  assert.equal(stickyPromptLabel('please run the tests'), 'You asked');
});
