// A status dot is a circle wherever it sits. In a flex row a shrinkable dot
// is squeezed narrower than it is tall — an ellipse — next to a long task
// name; the task switcher's dots were (its rows are flex, and the dot had no
// rule saying otherwise).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

function ruleBody(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, 'm'));
  assert.ok(match, `expected a rule for ${selector}`);
  return match[1];
}

test('no status dot gives way in a flex row', () => {
  assert.match(ruleBody('.status-dot'), /flex-shrink:\s*0;/);
});

test('the switcher\'s dots are sized and centred like the menu rows\' dots', () => {
  // One rule for both, so the two lists draw the same dot.
  const body = ruleBody('.task-switcher-item .status-dot,\n.tabs-pane-side .tab .status-dot');
  assert.match(body, /flex-shrink:\s*0;/);
  assert.match(body, /margin-top:\s*0;/);
  assert.match(body, /width:\s*10px;/);
  assert.match(body, /height:\s*10px;/);
  assert.match(body, /box-sizing:\s*border-box;/);
});
