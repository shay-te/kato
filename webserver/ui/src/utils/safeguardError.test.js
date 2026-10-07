// The JS safeguards-refusal detector mirrors the Python one
// (claude_core_lib/helpers/safeguard_error.py): same anchors, same fields.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  findSafeguardFlag,
  isSafeguardFlag,
  parseSafeguardFlag,
} from './safeguardError.js';

const REAL = [
  "API Error: Opus 5.5's safeguards flagged this session",
  '(https://www.anthropic.com/legal/aup). You may be seeing this for the',
  'first time: Opus 5.5 ... can sometimes flag non-cybersecurity work.',
  '',
  'Details: [cyber]',
  '',
  'Request ID: req_011CfkrVkTS5AF84rGjSAjWr',
].join('\n');

test('the real message is recognised and parsed', () => {
  assert.equal(isSafeguardFlag(REAL), true);
  assert.deepEqual(parseSafeguardFlag(REAL), {
    details: 'cyber',
    requestId: 'req_011CfkrVkTS5AF84rGjSAjWr',
    model: 'Opus 5.5',
  });
});

test('a reworded message with another model still matches', () => {
  const text = "Error: Fable 6's safeguards flagged the session — see "
    + 'https://www.anthropic.com/legal/aup';
  const flag = parseSafeguardFlag(text);
  assert.equal(flag.model, 'Fable 6');
  assert.equal(flag.details, '');
});

test('ordinary errors are not mistaken for a flag', () => {
  for (const text of ['', null, 'API Error: 529 overloaded', 'rate limit exceeded',
    'see https://www.anthropic.com/legal/aup', 'account flagged this session for billing']) {
    assert.equal(isSafeguardFlag(text), false);
    assert.equal(parseSafeguardFlag(text), null);
  }
});

function resultEntry(text, isError = true) {
  return { source: 'server', raw: { type: 'result', is_error: isError, result: text } };
}
function userEntry(text) {
  return { source: 'server', raw: { type: 'user', message: { content: text } } };
}

test('findSafeguardFlag fires only when the latest turn is a live flag', () => {
  // Latest result is a flag → flagged.
  assert.deepEqual(
    findSafeguardFlag([userEntry('do a thing'), resultEntry(REAL)]).details,
    'cyber',
  );
  // Latest result succeeded → not flagged, even if an older one was flagged.
  assert.equal(findSafeguardFlag([resultEntry(REAL), resultEntry('done', false)]), null);
  // A newer prompt was sent after the flag → the offer is stale.
  assert.equal(findSafeguardFlag([resultEntry(REAL), userEntry('try again please')]), null);
  // A newer LOCAL (just-typed) prompt also makes it stale.
  assert.equal(
    findSafeguardFlag([resultEntry(REAL), { source: 'local', kind: 'user', text: 'again' }]),
    null,
  );
  // No results at all, or non-flag error → null.
  assert.equal(findSafeguardFlag([userEntry('hi')]), null);
  assert.equal(findSafeguardFlag([resultEntry('rate limited')]), null);
  assert.equal(findSafeguardFlag([]), null);
  assert.equal(findSafeguardFlag(null), null);
});
