// Tests for ``extractAnswerableQuestions`` — the backend-AGNOSTIC detector
// that decides whether a tool call is an "answer a multiple-choice question"
// request (rendered as the answer form) vs a permission to grant. It keys on
// the questions SHAPE, never the tool name, so any backend emitting the shape
// gets the form. A false positive here would turn a real permission grant into
// an answer form; a false negative would show a scary grant for a question.

import assert from 'node:assert/strict';
import test from 'node:test';

import { extractAnswerableQuestions } from './answerableQuestion.js';

const CLAUDE_ASK = {
  questions: [
    {
      question: 'Which auth method?',
      header: 'Auth',
      options: [
        { label: 'OAuth', description: 'Use OAuth' },
        { label: 'API key', description: 'Use a key' },
      ],
      multiSelect: false,
    },
  ],
};

test('returns the questions for a well-formed AskUserQuestion payload', () => {
  const out = extractAnswerableQuestions(CLAUDE_ASK);
  assert.deepEqual(out, CLAUDE_ASK.questions);
});

test('is backend-agnostic: same shape with no/other tool name still detected', () => {
  // The detector never sees a tool name — only the input shape — so a
  // hypothetical non-Claude backend emitting the same payload renders too.
  const codexLike = {
    questions: [
      { question: 'Pick a region', options: [{ label: 'us' }, { label: 'eu' }] },
    ],
  };
  assert.deepEqual(extractAnswerableQuestions(codexLike), codexLike.questions);
});

test('multiSelect questions are detected', () => {
  const multi = {
    questions: [
      {
        question: 'Which features?',
        options: [{ label: 'a' }, { label: 'b' }],
        multiSelect: true,
      },
    ],
  };
  assert.deepEqual(extractAnswerableQuestions(multi), multi.questions);
});

test('mixed valid + malformed entries still render (form tolerates per-item)', () => {
  const mixed = {
    questions: [
      null,
      { question: 'Real?', options: [{ label: 'yes' }] },
    ],
  };
  // Returns the ORIGINAL array (form filters Boolean itself) because at least
  // one entry is well-formed.
  assert.deepEqual(extractAnswerableQuestions(mixed), mixed.questions);
});

test('null / undefined / non-object input → null', () => {
  assert.equal(extractAnswerableQuestions(null), null);
  assert.equal(extractAnswerableQuestions(undefined), null);
  assert.equal(extractAnswerableQuestions('nope'), null);
});

test('missing questions field → null', () => {
  assert.equal(extractAnswerableQuestions({ command: 'ls -la' }), null);
});

test('empty questions array → null', () => {
  assert.equal(extractAnswerableQuestions({ questions: [] }), null);
});

test('no false positive: a Bash/WebFetch grant input is not a question', () => {
  assert.equal(extractAnswerableQuestions({ command: 'rm -rf build' }), null);
  assert.equal(extractAnswerableQuestions({ url: 'https://x.test' }), null);
});

test('questions present but NOT question-shaped → null', () => {
  // A `questions` field that is a list of strings, or objects lacking the
  // question/options pair, must not be mistaken for an answer form.
  assert.equal(extractAnswerableQuestions({ questions: ['a', 'b'] }), null);
  assert.equal(
    extractAnswerableQuestions({ questions: [{ question: 'q only' }] }),
    null,
  );
  assert.equal(
    extractAnswerableQuestions({ questions: [{ options: [{ label: 'x' }] }] }),
    null,
  );
});

test('question with only whitespace text is not valid on its own', () => {
  assert.equal(
    extractAnswerableQuestions({ questions: [{ question: '   ', options: [] }] }),
    null,
  );
});
