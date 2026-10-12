// The pull-request count must never move the header it sits in: the list
// appears while someone reads, and a badge that took up width would re-flow
// the whole button row the moment the pull requests came in. Checked against
// the compiled stylesheet, since jsdom has no layout to measure.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

// The LAST rule for exactly ``selector`` — the one that wins: the list takes
// its panel from the shared popover rule and overrides where it opens.
function ruleBody(selector) {
  const start = css.lastIndexOf(`\n${selector} {`);
  assert.notEqual(start, -1, `no rule for ${selector}`);
  return css.slice(start, css.indexOf('}', start));
}

test('the count is laid over the button, which is its positioning box', () => {
  assert.match(ruleBody('.pull-request-list-badge'), /position:\s*absolute;/);
  assert.match(ruleBody('.pull-request-list-badge'), /pointer-events:\s*none;/);
  assert.match(ruleBody('.pull-request-list-toggle'), /position:\s*relative;/);
});

test('the list opens down from the header, at the button\'s right edge', () => {
  const list = ruleBody('.pull-request-list');
  assert.match(list, /top:\s*calc\(100% \+ 6px\);/);
  assert.match(list, /bottom:\s*auto;/);
  assert.match(list, /right:\s*0;/);
  assert.match(ruleBody('.pull-request-list-wrap'), /position:\s*relative;/);
});

test('open, the button\'s tooltip stays off the list', () => {
  assert.match(ruleBody('.pull-request-list-toggle[aria-expanded=true]::after'), /display:\s*none;/);
});
