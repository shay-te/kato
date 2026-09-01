// The inline permission ask must be answerable.
//
// Moving the ask out of a modal and into the chat put it in a column whose
// composer (``#message-form``) is ABSOLUTELY POSITIONED over the pane at
// z-index 10. An unbounded card therefore grew until its Allow/Deny row sat
// underneath the composer — the ask rendered in full, with no reachable way
// to answer it. Reported as "how on hell i can approve that?".
//
// Three things keep it answerable, and all three are asserted against the
// COMPILED css, because that is what the browser actually received:
//   1. the card clears the floating composer,
//   2. it is bounded and scrolls rather than growing without limit,
//   3. the decision row is pinned to its bottom edge.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

function ruleBody(selector) {
  const at = css.indexOf(selector);
  assert.ok(at !== -1, `no rule emitted for ${selector}`);
  const open = css.indexOf('{', at);
  const close = css.indexOf('}', open);
  return css.slice(open + 1, close);
}

test('the card leaves room for the floating composer', () => {
  // Reuses --composer-h / --queued-h, published by MessageForm and the
  // queued list, rather than a magic number — so the offset tracks a
  // composer that grows a second input line or a queue that gains rows.
  const body = ruleBody('.modal.is-inline {');
  assert.match(body, /bottom:\s*calc\([^;]*var\(--composer-h/);
  assert.match(body, /var\(--queued-h/);
});

test('the card sits ABOVE the composer, not in flow beside it', () => {
  // ``#message-form`` is absolutely positioned over the chat. A card left in
  // normal flow competes with something that is not in flow: whatever margin
  // it reserves, the composer still paints over its bottom edge — and the
  // bottom edge is where Deny / Allow live.
  const body = ruleBody('.modal.is-inline {');
  assert.match(body, /position:\s*absolute/);
  assert.match(body, /bottom:\s*calc\(/);
  // Under the composer, so the input stays reachable with an ask open.
  assert.match(body, /z-index:\s*9\b/);
});

test('the cap and the overflow live on the SAME element', () => {
  // They must not be split across a wrapper and its child. An earlier attempt
  // capped the wrapper with ``50%``, which resolves against a parent with no
  // definite height — so it silently did nothing, the card grew to full
  // height, and its top ran off the pane. ``vh`` is always definite.
  const body = ruleBody('.modal.is-inline .modal-card {');
  assert.match(body, /overflow-y:\s*auto/);
  assert.match(body, /max-height:\s*\d+vh/);
  assert.match(body, /min-height:\s*0/);
  // The wrapper must NOT also carry a cap, or the two can disagree.
  const wrapper = ruleBody('.modal.is-inline {');
  assert.doesNotMatch(wrapper, /max-height/);
});

test('the decision row is pinned to the bottom of the card', () => {
  // THE BUG. Everything above it can be taller than the pane, and the two
  // buttons are the entire point of the block.
  const body = ruleBody('.modal.is-inline .modal-actions {');
  assert.match(body, /position:\s*sticky/);
  assert.match(body, /bottom:\s*0/);
  // Opaque, or the scrolling content shows through the buttons.
  assert.match(body, /background:\s*#/);
});
