import assert from 'node:assert/strict';
import test from 'node:test';

import { PLAN_SPLIT_PROMPT_LABEL, isPlanSplitPrompt } from './planQueuePrompt.js';

test('the split request is kato\'s, wherever its header line sits', () => {
  assert.equal(isPlanSplitPrompt('Kato plan queue · split the plan\n\nSplit the approved plan…'), true);
  // A respawned chat gets kato's workspace preamble first.
  assert.equal(isPlanSplitPrompt('WORKSPACE SCOPE …\n\nKato plan queue · split the plan\n\n…'), true);
  assert.equal(PLAN_SPLIT_PROMPT_LABEL, 'Kato · plan split');
});

test('the operator\'s own words are never taken for it', () => {
  for (const text of ['', null, 'please split the plan', 'Kato plan queue · split the plan into 3', 'x Kato plan queue · split the plan']) {
    assert.equal(isPlanSplitPrompt(text), false, String(text));
  }
});
