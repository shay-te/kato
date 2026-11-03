import test from 'node:test';
import assert from 'node:assert/strict';

import { resultFailureText } from './resultFailure.js';

test('the reason in ``result`` is shown as it is', () => {
  assert.equal(resultFailureText({ is_error: true, result: ' rate limited ' }), 'rate limited');
});

test('a failure during execution carries its reason in ``errors``', () => {
  // The event the real CLI sends, word for word: no ``result`` at all.
  const event = {
    subtype: 'error_during_execution',
    is_error: true,
    errors: ['No conversation found with session ID: 00000000-0000-4000-8000-000000000000'],
  };
  assert.equal(
    resultFailureText(event),
    'No conversation found with session ID: 00000000-0000-4000-8000-000000000000',
  );
});

test('several errors are all shown, and blanks are dropped', () => {
  assert.equal(resultFailureText({ errors: ['one', ' ', null, 'two'] }), 'one\ntwo');
});

test('``result`` wins over ``errors``', () => {
  assert.equal(resultFailureText({ result: 'first', errors: ['second'] }), 'first');
});

test('with no reason on the event, the agent\'s last error output is shown', () => {
  assert.equal(
    resultFailureText({ subtype: 'error_during_execution', errors: [], failure_detail: 'API Error: 529' }),
    'API Error: 529',
  );
});

test('with nothing at all, it still says what happened, never just "failed"', () => {
  assert.match(resultFailureText({ subtype: 'error_during_execution' }), /stopped with an error while working/);
  assert.match(resultFailureText({ subtype: 'error_max_turns' }), /limit of turns/);
  assert.match(resultFailureText({ subtype: 'error_max_budget_usd' }), /spending limit/);
  assert.match(resultFailureText({ subtype: 'something_new' }), /gave no reason/);
  assert.match(resultFailureText({}), /gave no reason/);
  assert.match(resultFailureText(null), /gave no reason/);
});

test('a result that is not a list of errors is ignored, not shown raw', () => {
  assert.match(resultFailureText({ errors: 'oops' }), /gave no reason/);
});
