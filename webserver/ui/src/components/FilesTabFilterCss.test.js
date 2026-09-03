// The file-search box must stay readable once the repo dropdown is beside it.
//
// The filter row held the search field and the "All repos" picker on one
// line unconditionally, with the field taking whatever was left over. In a
// narrow files pane with the picker present that was a capsule barely wide
// enough to see what you were typing — reported as "it is too hard to type
// and see things there after the addition of the dropdown".
//
// The row wraps now: the field keeps a usable width and the picker drops to
// its own line when there is no room beside it. One extra row of height, and
// only when it is actually needed.
//
// Asserted against the COMPILED stylesheet — that is what the browser got,
// and a later declaration can silently cancel an earlier one (which is
// exactly what happened while writing this).

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

test('the filter row is allowed to wrap', () => {
  assert.match(ruleBody('.files-tab-filter {'), /flex-wrap:\s*wrap/);
});

test('the field keeps a usable width instead of being squeezed', () => {
  // A flex-basis, not a width: it still grows to fill a wide pane, but never
  // collapses below the basis — it takes the whole row instead.
  const body = ruleBody('.files-tab-filter-field {');
  assert.match(body, /flex:\s*1\s+1\s+\d+px/);
});

test('the field is the positioning context for its icon and clear button', () => {
  // Both are absolutely positioned. Anchored to the ROW they would float
  // over whichever line laid out first, and the icon would centre itself
  // across both lines once it wrapped.
  assert.match(ruleBody('.files-tab-filter-field {'), /position:\s*relative/);
  assert.match(ruleBody('.files-tab-filter-icon {'), /position:\s*absolute/);
  assert.match(ruleBody('.files-tab-filter-clear {'), /position:\s*absolute/);
});

test('the repo picker wraps rather than shrinking to nothing', () => {
  assert.match(ruleBody('.files-tab-filter-scope {'), /flex-shrink:\s*0/);
});

// ---------------------------------------------------------------------------
// One height for the whole row.
//
// The search field, the repo picker and the round buttons were each sized a
// different way — vertical padding on the field, smaller padding on the
// picker, a fixed box on the buttons — and came out three different heights,
// which is what made the row look unfinished.
//
// $ICON-BOX-LG (28px) is the buttons' own size, so it is the one that cannot
// change without redrawing them; the other two are pinned to it.
// ---------------------------------------------------------------------------

test('the search field is the shared control height', () => {
  assert.match(ruleBody('.files-tab-filter-input {'), /height:\s*28px/);
});

test('the repo picker is the shared control height', () => {
  assert.match(ruleBody('.files-tab-filter-scope {'), /height:\s*28px/);
});

test('the round buttons still define that height', () => {
  const body = ruleBody('.files-tab-icon-btn,');
  assert.match(body, /height:\s*28px/);
  assert.match(body, /width:\s*28px/);
});

test('the field centres by height, not by vertical padding', () => {
  // Padding-based centring is what made it taller than its neighbours; the
  // horizontal padding must survive, since it reserves room for the leading
  // icon and the clear button.
  const body = ruleBody('.files-tab-filter-input {');
  assert.match(body, /padding:\s*0 \d+px 0 \d+px/);
});
