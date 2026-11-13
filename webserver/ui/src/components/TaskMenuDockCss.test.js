// A pinned tasks menu is docked and resizable — checked against the COMPILED
// stylesheet (a Sass slip can leave ``#{…}`` unexpanded and still build).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const css = readFileSync(
  join(new URL('.', import.meta.url).pathname, '../../../static/css/app.css'),
  'utf8',
);

function ruleBody(selector) {
  const start = css.indexOf(`\n${selector} {`);
  assert.notEqual(start, -1, `no rule for ${selector}`);
  return css.slice(start, css.indexOf('}', start));
}

test('the page gives up the docked width when pinned, the collapsed rail otherwise', () => {
  assert.match(ruleBody('#root'), /padding-left:\s*var\(--task-rail-docked-width,\s*118px\)/);
});

test('the open menu is as wide as it was dragged to', () => {
  assert.match(ruleBody('.tabs-pane-side.is-open'), /width:\s*min\(var\(--task-rail-open-width,\s*360px\),\s*90vw\)/);
});

test('docked, it casts no shadow over a page that moved aside', () => {
  assert.match(ruleBody('.tabs-pane-side.is-docked'), /box-shadow:\s*none/);
});

test('its edge is a resize handle, and the width follows a drag with no easing', () => {
  assert.match(ruleBody('.tabs-pane-resizer'), /cursor:\s*col-resize/);
  assert.match(ruleBody('.tabs-pane-side.is-resizing'), /transition:\s*none/);
});
