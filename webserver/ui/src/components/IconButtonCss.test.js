// Icon buttons are bare glyphs; the circle appears on hover.
//
// They used to carry a filled background and a 1px border at rest, so a row
// of them read as a row of BUTTONS competing with the content beside them —
// and each paid for that chrome in width. Bare glyphs give the strip back the
// room, which is the point: more actions fit, and the ones present stay quiet
// until you reach for one.
//
// Three families share this (the tab strip, the task header, the files /
// changes toolbars). They were already near-identical copies, so they are
// asserted TOGETHER — a change that lands in one and not the others is the
// failure this guards against.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

function ruleBody(selector) {
  const at = css.indexOf(selector);
  assert.ok(at !== -1, `no rule emitted for ${selector}`);
  const open = css.indexOf('{', at);
  return css.slice(open + 1, css.indexOf('}', open));
}

const RESTING = [
  '.tabs-action {',
  '.session-action {',
  '.files-tab-icon-btn,',
];

const HOVER = [
  '.tabs-action:hover:not(:disabled) {',
  '.session-action:hover {',
  '.files-tab-icon-btn:hover,',
];

for (const selector of RESTING) {
  test(`${selector} is bare at rest`, () => {
    const body = ruleBody(selector);
    assert.match(body, /background:\s*transparent/);
  });

  test(`${selector} keeps its border box so hover does not shift it`, () => {
    // ``transparent``, not removed: the 1px still occupies space, so painting
    // it on hover cannot nudge the icon by a pixel.
    assert.match(ruleBody(selector), /border:\s*1px solid transparent/);
  });
}

for (const selector of HOVER) {
  test(`${selector} paints the circle`, () => {
    const body = ruleBody(selector);
    assert.match(body, /background:\s*rgba\(/);
    assert.doesNotMatch(body, /background:\s*transparent/);
  });
}

test('the resting fill is weaker than the hover fill', () => {
  // Bare means bare. If the two ever converge the affordance is gone: there
  // is no way to tell a hovered control from a resting one.
  for (const selector of RESTING) {
    assert.doesNotMatch(ruleBody(selector), /background:\s*rgba\(/);
  }
});

test('a disabled control is not left looking enabled', () => {
  // It used to get a FILL when disabled — which, now that resting is bare,
  // would make the disabled state the loudest of the three.
  for (const selector of ['.tabs-action:disabled {', '.session-action:disabled {']) {
    assert.match(ruleBody(selector), /background:\s*transparent/);
  }
});
