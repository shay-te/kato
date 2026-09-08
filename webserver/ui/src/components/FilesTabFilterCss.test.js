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

test('the search owns the whole top row of the files header', () => {
  // ``.files-tab-filter`` is a flex CHILD of the header, so a 100% basis on
  // the FIELD only ever filled the filter box — about half the header. That
  // is why the field stayed a small pill beside the action buttons even after
  // it was told to be full width. The header has to wrap, and the filter has
  // to claim the row.
  assert.match(ruleBody('.files-tab-header {'), /flex-wrap:\s*wrap/);
  assert.match(ruleBody('.files-tab-filter {'), /flex:\s*1\s+1\s+100%/);
  assert.match(ruleBody('.files-tab-filter-field {'), /flex:\s*1\s+1\s+100%/);
});

test('the search controls are in normal flow, never overlaid', () => {
  // Absolute right-offsets put the two toggles 6px apart and let them ride
  // over the placeholder in a narrow pane — the reported "Aa ab" on top of
  // "Search files…" and on each other. A flex group cannot overlap anything.
  const actions = ruleBody('.files-tab-filter-actions {');
  assert.match(actions, /display:\s*inline-flex/);
  // Scoped under the actions group for SPECIFICITY: ``header
  // button:not(.header-status)`` (0,1,2) styles every button inside a <header>
  // as a 28px circle, and a bare class (0,1,0) lost to it — which is how the
  // two text toggles rendered as big blue circles.
  const toggle = ruleBody('.files-tab-filter-actions .files-tab-filter-toggle {');
  assert.doesNotMatch(toggle, /position:\s*absolute/);
  assert.match(toggle, /border-radius:\s*4px/);
  assert.match(toggle, /width:\s*auto/);
  assert.doesNotMatch(ruleBody('.files-tab-filter-clear {'), /position:\s*absolute/);
  // ...and the input must yield to them instead of claiming the full width.
  assert.match(ruleBody('.files-tab-filter-input {'), /min-width:\s*0/);
});

test('the field is the positioning context for its icon and clear button', () => {
  // Both are absolutely positioned. Anchored to the ROW they would float
  // over whichever line laid out first, and the icon would centre itself
  // across both lines once it wrapped.
  assert.match(ruleBody('.files-tab-filter-field {'), /position:\s*relative/);
  assert.match(ruleBody('.files-tab-filter-icon {'), /position:\s*absolute/);
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


test('the on-state reuses the app\'s existing selected-chip colours', () => {
  // Operator: "the round blue is ugly, use gray/blue background color, use
  // existing colors". Same fill + hairline the header status chip already uses
  // for its active state — not a new saturated ring.
  const on = ruleBody('.files-tab-filter-actions .files-tab-filter-toggle.is-on {');
  assert.match(on, /background:\s*rgba\(10, 132, 255, 0\.18\)/);
  assert.match(on, /border-color:\s*rgba\(10, 132, 255, 0\.4\)/);
});
