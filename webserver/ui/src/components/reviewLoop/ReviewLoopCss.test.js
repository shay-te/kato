// The loop's marks must never move anything the operator is reading.
//
// A loop starts and ends while someone is mid-task. If the tab mark or the
// header dot took up width, the whole tab strip / header row would re-flow at
// that moment. They are laid OVER their host instead — checked against the
// compiled stylesheet, since jsdom has no layout to measure.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const css = readFileSync(
  join(new URL('..', import.meta.url).pathname, '../../../static/css/app.css'),
  'utf8',
);

// Every rule for exactly ``selector``, joined: a selector may be declared in
// more than one place, and the property under test can live in any of them.
function ruleBody(selector) {
  const bodies = [];
  let start = css.indexOf(`\n${selector} {`);
  while (start !== -1) {
    bodies.push(css.slice(start, css.indexOf('}', start)));
    start = css.indexOf(`\n${selector} {`, start + 1);
  }
  assert.notEqual(bodies.length, 0, `no rule for ${selector}`);
  return bodies.join('\n');
}

test('the tab mark is laid over the pill, which is its positioning box', () => {
  assert.match(ruleBody('.tab-review-loop-badge'), /position:\s*absolute;/);
  assert.match(ruleBody('.tab-review-loop-badge'), /pointer-events:\s*none;/);
  assert.match(ruleBody('.tabs-pane-top .tab'), /position:\s*relative;/);
});

test('the header dot is laid over its button, which keeps its own size', () => {
  assert.match(ruleBody('.review-loop-dot'), /position:\s*absolute;/);
  assert.match(ruleBody('.review-loop-button'), /position:\s*relative;/);
});

test('the chip gives way inside the title area instead of pushing the actions', () => {
  assert.match(ruleBody('.review-loop-chip'), /flex-shrink:\s*0;/);
  assert.match(ruleBody('.review-loop-chip'), /white-space:\s*nowrap;/);
});

test('the pane\'s controls hold the trailing edge, running or not', () => {
  // Stop while a loop runs; the round limit + Start / Run again otherwise —
  // one group, pushed right, so the controls never trade places.
  assert.match(ruleBody('.review-loop-pane-actions'), /margin-left:\s*auto;/);
  assert.match(ruleBody('.review-loop-pane-actions'), /flex-shrink:\s*0;/);
});

test('the pane header: the title keeps its words, the status gives way', () => {
  assert.match(ruleBody('.review-loop-pane-title'), /flex-shrink:\s*0;/);
  assert.match(ruleBody('.review-loop-pane-title'), /white-space:\s*nowrap;/);
  assert.doesNotMatch(ruleBody('.review-loop-pane-title'), /flex:\s*1;/);
  assert.match(ruleBody('.review-loop-pane-status'), /flex:\s*1 1 auto;/);
  assert.match(ruleBody('.review-loop-pane-status'), /text-overflow:\s*ellipsis;/);
});

test('"Up to [N rounds]" reads on one line', () => {
  // It once shared its class with the list of rounds (a column) and stacked.
  const picker = ruleBody('.review-loop-round-limit');
  assert.match(picker, /display:\s*inline-flex;/);
  assert.doesNotMatch(picker, /flex-direction:\s*column;/);
});


test('a running loop rings the whole task pill (overlay ::after, own axis)', () => {
  const ring = ruleBody('.tabs-pane-top .tab.has-review-loop::after');
  assert.match(ring, /position:\s*absolute;/);
  assert.match(ring, /border:\s*1\.5px solid/);
  assert.match(ring, /pointer-events:\s*none;/);
  // Overlaid, so it layers over the active/attention backgrounds without
  // touching the status dot's own box-shadow.
  assert.match(ring, /inset:\s*-2px;/);
});

test('a loop tab in the shared strip reads as its own kind', () => {
  // Indigo active underline (not the files' cyan), and the loop glyph tinted
  // by the loop's state.
  assert.match(ruleBody('.file-tab.is-review-loop.active'), /border-bottom-color:\s*#a78bfa;/);
  assert.match(ruleBody('.file-tab-loop-icon.is-running'), /color:\s*#a78bfa;/);
  assert.match(ruleBody('.file-tab-loop-icon.is-good'), /color:/);
});

test('a round\'s duration sits beside its outcome, muted, in even digits', () => {
  const body = ruleBody('.review-loop-round-duration');
  assert.match(body, /font-variant-numeric: tabular-nums/);
  assert.match(body, /color:/);
});
