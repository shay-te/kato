import assert from 'node:assert/strict';
import test from 'node:test';

import { appendComposerFragment } from './chatComposerHelpers.js';


test('appendComposerFragment inserts a file path into an empty input', function () {
  const result = appendComposerFragment('', 'src/App.jsx');

  assert.equal(result, 'src/App.jsx');
});

test('appendComposerFragment separates an existing message from a file path', function () {
  const result = appendComposerFragment('please inspect', 'src/App.jsx');

  assert.equal(result, 'please inspect src/App.jsx');
});

test('appendComposerFragment keeps existing trailing whitespace before a file path', function () {
  const result = appendComposerFragment('please inspect\n', 'src/App.jsx');

  assert.equal(result, 'please inspect\nsrc/App.jsx');
});
