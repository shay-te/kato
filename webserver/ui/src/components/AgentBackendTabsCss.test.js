// The status chip must read as part of its tab, not as a separate badge.
//
// Two properties the operator actually notices: it is the SAME text size as
// the tab name beside it, and it is centred against that name rather than
// sitting on its baseline (a bordered pill on a baseline rides visibly low).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const css = readFileSync(
  join(new URL('..', import.meta.url).pathname, '../../static/css/app.css'),
  'utf8',
);

function ruleBody(selector) {
  const start = css.indexOf(`\n${selector} {`);
  assert.notEqual(start, -1, `no rule for ${selector}`);
  return css.slice(start, css.indexOf('}', start));
}

test('the chip inherits the tab name’s text size', () => {
  // Inherited, not pinned: a pinned value silently drifts the day the tab's
  // own size changes.
  assert.match(ruleBody('.agent-backend-tab-status'), /font-size:\s*inherit;/);
});

test('the tab centres its contents rather than baselining them', () => {
  const body = ruleBody('.agent-backend-tab-button');
  assert.match(body, /display:\s*inline-flex;/);
  assert.match(body, /align-items:\s*center;/);
});

test('the chip does not re-introduce a baseline alignment', () => {
  assert.doesNotMatch(
    ruleBody('.agent-backend-tab-status'), /vertical-align:/,
  );
});

test('the chip is still a pill, not a plain word', () => {
  const body = ruleBody('.agent-backend-tab-status');
  assert.match(body, /border-radius:\s*999px;/);
  assert.match(body, /border:\s*1px solid/);
});
