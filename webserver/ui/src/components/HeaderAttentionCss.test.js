// The "a task needs you" pill must stay readable in the header.
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

// Every item that shares the header row with the attention pill. If any one
// of them can shrink, it is the one that collides with its neighbour.
const NON_SHRINKING = [
  'header h1',
  'header .subtitle',
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

test('the status pill is the one thing allowed to give up space', () => {
  // Something has to absorb the slack or the row cannot fit at all. The
  // status line is the right victim: it already ellipsises, so losing width
  // degrades it gracefully instead of overlapping anything.
  const body = rulesFor('header .header-status');
  assert.match(body, /flex:\s*1/);
});

test('the status text truncates instead of spilling', () => {
  const body = rulesFor('header .header-status-text');
  assert.match(body, /text-overflow:\s*ellipsis/);
  assert.match(body, /overflow:\s*hidden/);
  assert.match(body, /min-width:\s*0/);
});

test('the task id is not set in the smallest available type', () => {
  // A task id ("UNA-2897") is read character by character, next to 26px
  // title text. At 11px it read as noise rather than as a control.
  const body = rulesFor('.permission-roster-chip');
  const size = /font-size:\s*(\d+)px/.exec(body);
  assert.ok(size, 'the chip sets no font-size');
  assert.ok(
    Number(size[1]) >= 12,
    `chip type is ${size[1]}px — too small to read a task id beside 26px text`,
  );
});

test('the decorative subtitle yields before the pill does', () => {
  // When the bar genuinely runs out of room, "Planning UI" is what goes —
  // it is decoration, and the task waiting on an answer is not.
  const at = css.indexOf('@media (max-width: 900px)');
  assert.notEqual(at, -1, 'no narrow-width rule for the header subtitle');
  const block = css.slice(at, at + 200);
  assert.match(block, /header \.subtitle/);
  assert.match(block, /display:\s*none/);
});
