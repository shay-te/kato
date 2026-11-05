import test from 'node:test';
import assert from 'node:assert/strict';

import { refusedToolsText, resultFailureText } from './resultFailure.js';

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


test('a turn limit says where it is set: it is never kato\'s default', () => {
  const text = resultFailureText({
    subtype: 'error_max_turns', is_error: true, errors: ['Reached maximum number of turns (25)'],
  });
  assert.equal(text.split('\n')[0], 'Reached maximum number of turns (25)');
  assert.match(text, /Settings → General → Max turns \(empty = no limit\)/);
  assert.doesNotMatch(resultFailureText({ result: 'rate limited' }), /Max turns/);
});

// The report: "an error about permission to edit a file, but it never asked".
test('what the CLI refused without asking is listed, each target once with its count', () => {
  const text = refusedToolsText({
    permission_denials: [
      { tool_name: 'Edit', tool_input: { file_path: 'src/app.ts' } },
      { tool_name: 'Edit', tool_input: { file_path: 'src/app.ts' } },
      { tool_name: 'Write', tool_input: { file_path: 'notes.txt', content: 'x' } },
      { tool_name: 'Bash', tool_input: { command: `npm test ${'x'.repeat(200)}` } },
      { tool_name: 'WebFetch', tool_input: {} },
      { tool_input: { file_path: 'nameless.txt' } },
    ],
  });
  const lines = text.split('\n');
  assert.equal(lines[0], 'Refused without asking you (5):');
  assert.equal(lines[1], '• Edit src/app.ts ×2');
  assert.equal(lines[2], '• Write notes.txt');
  assert.ok(lines[3].startsWith('• Bash npm test xx') && lines[3].endsWith('…') && lines[3].length < 90);
  assert.equal(lines[4], '• WebFetch');
  assert.match(lines.at(-1), /background task cannot ask you.*Deny always/);
});

test('many refused targets are cut to a few, and none is nothing', () => {
  const denials = Array.from({ length: 8 }, (_, i) => ({ tool_name: 'Edit', tool_input: { file_path: `f${i}` } }));
  const lines = refusedToolsText({ permission_denials: denials }).split('\n');
  assert.equal(lines[0], 'Refused without asking you (8):');
  assert.equal(lines[6], '• …and 3 more');
  assert.equal(refusedToolsText({ permission_denials: [] }), '');
  assert.equal(refusedToolsText({ permission_denials: 'oops' }), '');
  assert.equal(refusedToolsText(null), '');
});
