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

// Anchored at a TOP-LEVEL selector (start of line in expanded sass output).
// A bare ``indexOf`` also matches the same class inside a DESCENDANT selector
// — ``.files-tab-filter-field:not(...) .files-tab-filter-clear`` — so once a
// nested rule was emitted earlier in the file, these assertions silently
// started reading the wrong block (it returned ``display: none`` for the
// clear button and the position assertion failed for the right reason on the
// wrong rule).
function ruleBody(selector) {
  const at = css.indexOf(`\n${selector}`);
  assert.ok(at !== -1, `no top-level rule emitted for ${selector}`);
  const open = css.indexOf('{', at);
  return css.slice(open + 1, css.indexOf('}', open));
}

test('the filter row is allowed to wrap', () => {
  assert.match(ruleBody('.files-tab-filter {'), /flex-wrap:\s*wrap/);
});

test('the field is a small pill at rest and the whole row when in use', () => {
  // Spotlight-style. The pane is narrow, and the field shared its line with
  // the repo picker and five buttons — there was no room for the search text
  // AND the Match case / Exact toggles, so the toggles rendered on top of the
  // placeholder. Collapsed it is one icon wide; focused it takes a 100% basis
  // and the wrapping row pushes everything else to the next line.
  assert.match(ruleBody('.files-tab-filter-field {'), /flex:\s*0\s+0\s+28px/);
  assert.match(ruleBody('.files-tab-filter-field:focus-within,'), /flex:\s*1\s+1\s+100%/);
});

test('a live query keeps the field open after blur', () => {
  // Collapsing a field that is still FILTERING the tree hides why the tree
  // looks the way it does — ``is-active`` shares the focused rule.
  const at = css.indexOf('.files-tab-filter-field:focus-within,');
  const selector = css.slice(at, css.indexOf('{', at));
  assert.match(selector, /\.files-tab-filter-field\.is-active/);
});

test('collapsed, nothing renders on top of the icon', () => {
  // The placeholder, the toggles and the clear button all live inside a field
  // that is one icon wide when closed. This is the rule that stops them
  // overlapping — the reported "Aa ab" sitting across "Search files…".
  const at = css.indexOf('.files-tab-filter-field:not(:focus-within):not(.is-active)');
  assert.ok(at !== -1, 'no collapsed-state rules emitted');
  const block = css.slice(at, at + 700);
  assert.match(block, /::placeholder[\s\S]{0,60}opacity:\s*0/);
  assert.match(block, /filter-toggle[\s\S]{0,120}display:\s*none/);
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
