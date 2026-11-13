// ``kato:auto-wait`` rows: a green border in the tasks rail, collapsed or not.
// Checked against the compiled stylesheet (jsdom has no cascade to measure):
// the rule exists, it is green, it comes after the rims it must beat (hover,
// the task on screen), a task waiting for you still beats it, and the
// collapsed rail does not strip it.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { AUTOMATION_LEVEL } from '../constants/automationLevel.js';

const css = readFileSync(
  join(new URL('.', import.meta.url).pathname, '../../../static/css/app.css'),
  'utf8',
);

const AUTO_WAIT = '\n.tabs-pane-side .tab.is-auto-wait {';

function at(selector) {
  const index = css.indexOf(`\n${selector} {`);
  assert.notEqual(index, -1, `no rule for ${selector}`);
  return index;
}

test('an auto-wait row has a green border', () => {
  const start = css.indexOf(AUTO_WAIT);
  assert.notEqual(start, -1);
  assert.match(css.slice(start, css.indexOf('}', start)), /border-color:\s*#34d399;/);
});

test('it beats hover and the task on screen, and a task waiting for you beats it', () => {
  const autoWait = at('.tabs-pane-side .tab.is-auto-wait');
  assert.ok(autoWait > at('.tabs-pane-side .tab:hover'));
  assert.ok(autoWait > at('.tabs-pane-side .tab.active'));
  assert.ok(autoWait < at('.tabs-pane-side .tab.needs-attention'));
});

test('the collapsed rail keeps its box, as it does for the task on screen', () => {
  assert.match(
    css,
    /\.tabs-pane-side:not\(\.is-open\) \.tab:not\(\.active\):not\(\.needs-attention\):not\(\.is-pinned\):not\(\.is-auto-wait\):not\(:hover\) \{/,
  );
});

test('the level the row reads is the one the server sends', () => {
  assert.equal(AUTOMATION_LEVEL.AUTO_WAIT, 'auto-wait');
  assert.equal(AUTOMATION_LEVEL.AUTOPILOT, 'autopilot');
  assert.ok(Object.isFrozen(AUTOMATION_LEVEL));
});
