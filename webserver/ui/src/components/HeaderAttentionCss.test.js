// The "a task needs you" pill must stay readable (now in the tasks rail's foot;
// it was in the app header, which is gone).
//
// Reported as "fix this pill on top for tasks waiting to be answered, it's
// not readable at all", with a screenshot showing the app title running
// straight into the task id: "…ning UIUNA-2897".
//
// Cause: flex children default to `flex-shrink: 1`, and an `inline-flex` box
// shrunk below its own text does NOT reflow — the text spills past the box's
// edge and lands on top of the next item. `.permission-roster` had already
// been pinned to `flex-shrink: 0` for exactly this reason ("shrinking a
// nowrap row does not reflow it, it just makes the children overlap"), but
// the title and subtitle beside it had not, so they were the ones that
// collapsed instead.
//
// Asserted against the COMPILED stylesheet: the vitest tier loads no CSS and
// jsdom has no layout engine, so neither can see this class of bug at all.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

function rulesFor(selector) {
  const found = [];
  const pattern = /([^{}]+)\{([^{}]*)\}/g;
  let match = pattern.exec(css);
  while (match !== null) {
    const selectors = match[1].split(',').map((s) => s.trim());
    if (selectors.includes(selector)) found.push(match[2]);
    match = pattern.exec(css);
  }
  return found.join(';');
}

// The chips moved to the tasks rail's foot (there is no app bar), but the
// rule stands: a nowrap item that can shrink overlaps its neighbour.
const NON_SHRINKING = [
  '.task-menu-title',
  '.permission-roster',
  '.permission-roster-chip',
];

for (const selector of NON_SHRINKING) {
  test(`${selector} never shrinks into its neighbour`, () => {
    const body = rulesFor(selector);
    assert.notEqual(body, '', `no rule emitted for ${selector}`);
    assert.match(
      body,
      /flex-shrink:\s*0/,
      `${selector} can be shrunk below its text, which makes it overlap`,
    );
  });
}

test('the status text truncates instead of spilling', () => {
  const body = rulesFor('.task-menu-footer .header-status-text');
  assert.match(body, /text-overflow:\s*ellipsis/);
  assert.match(body, /overflow:\s*hidden/);
  assert.match(body, /min-width:\s*0/);
});

test('the task id is not set in the smallest available type', () => {
  // A task id ("UNA-2897") is read character by character. At 11px it read as
  // noise rather than as a control.
  const body = rulesFor('.permission-roster-chip');
  const size = /font-size:\s*(\d+)px/.exec(body);
  assert.ok(size, 'the chip sets no font-size');
  assert.ok(Number(size[1]) >= 12, `chip type is ${size[1]}px — too small to read a task id`);
});

test('the chips wrap within the collapsed rail, expanded too, so none moves under the pointer', () => {
  const body = rulesFor('.permission-roster');
  assert.match(body, /flex-wrap:\s*wrap/);
  assert.match(body, /max-width:\s*106px/);  // $TASK-RAIL-INNER
});

test('the chip is never narrower than the id it carries', () => {
  // "the text should fit in the buttons" — a chip that renders narrower than
  // its id has clipped the one thing it exists to say.
  assert.match(rulesFor('.permission-roster-chip'), /min-width:\s*max-content/);
});

test('the chip dot cannot be squeezed into an ellipse', () => {
  // Every other part of the chip carried a shrink guard; the dot did not, and
  // a flex child defaults to shrink:1.
  assert.match(rulesFor('.permission-roster-dot'), /flex-shrink:\s*0/);
});
