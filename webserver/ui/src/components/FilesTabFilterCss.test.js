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
  // A SOFTENED SQUARE. Both extremes read wrong in place: 4px was too hard
  // against a fully-rounded container, and a pill turned a 20px-tall box into
  // an oval. 6px on 20px sits inside the capsule without competing with it.
  assert.match(toggle, /border-radius:\s*6px/);
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

test('the repo picker has room for its label and its chevron', () => {
  // 4px put the label hard against the border on one side and against the
  // native chevron on the other, and a 120px cap clipped a real repository id
  // like ``ob-love-admin-backend``.
  const body = ruleBody('.files-tab-filter-scope {');
  assert.match(body, /padding:\s*0\s+10px/);
  assert.match(body, /max-width:\s*160px/);
  // Capsule, like the search field above it — the row reads as one family.
  assert.match(body, /border-radius:\s*999px/);
  // ...and the radius only STICKS with the native chrome off: a platform
  // <select> keeps its own rounded-rect and ignores border-radius, which is
  // why the picker still had square-ish corners beside a rounded field.
  assert.match(body, /appearance:\s*none/);
  // Dropping the native look drops the platform chevron, so one is drawn —
  // and the right padding has to clear it, not just the curve.
  assert.match(body, /background-image:\s*url/);
  assert.match(body, /padding-right:\s*24px/);
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
  // The FIELD owns the capsule now (border, background, height) so the
  // toggles can sit inside it as ordinary flex children, the way VS Code
  // draws them. The input fills it and carries no chrome of its own.
  assert.match(ruleBody('.files-tab-filter-field {'), /height:\s*28px/);
  assert.match(ruleBody('.files-tab-filter-input {'), /height:\s*100%/);
  assert.match(ruleBody('.files-tab-filter-input {'), /border:\s*none/);
});

test('the capsule keeps its focus ring while a toggle is clicked', () => {
  // ``:focus-within``, not ``:focus`` on the input — clicking a toggle moves
  // focus off the input but stays inside the field, and the box must not
  // flicker out of its focus state while the operator uses the controls in it.
  const ring = ruleBody('.files-tab-filter-field:focus-within {');
  assert.match(ring, /border-color:\s*rgba\(10, 132, 255/);
  assert.match(ring, /box-shadow:/);
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
  // Padding-based centring is what made it taller than its neighbours. The
  // LEFT inset must survive — it clears the leading search icon; the right
  // side is now the toggles' own gap inside the capsule.
  assert.match(ruleBody('.files-tab-filter-input {'), /padding:\s*0 0 0 \d+px/);
  assert.match(ruleBody('.files-tab-filter-field {'), /padding-right:\s*\d+px/);
});


test('the on-state reuses the app\'s existing selected-chip colours', () => {
  // Operator: "the round blue is ugly, use gray/blue background color, use
  // existing colors". Same fill + hairline the header status chip already uses
  // for its active state — not a new saturated ring.
  const on = ruleBody('.files-tab-filter-actions .files-tab-filter-toggle.is-on {');
  assert.match(on, /background:\s*rgba\(10, 132, 255, 0\.18\)/);
  assert.match(on, /border-color:\s*rgba\(10, 132, 255, 0\.4\)/);
});

test('the exact toggle carries VS Code\'s underlined "ab" icon', () => {
  // The underline IS the icon in VS Code's Match Whole Word button. Without
  // it the two toggles are just two pairs of letters with nothing telling
  // them apart at a glance.
  const body = ruleBody('.files-tab-filter-actions .files-tab-filter-toggle.is-word {');
  assert.match(body, /text-decoration:\s*underline/);
});

test('the search row can shrink in a narrow pane', () => {
  // A flex ITEM defaults to ``min-width: auto`` — it refuses to shrink below
  // its content. The field said it could shrink, but its wrapper could not,
  // so in a narrow pane the capsule was laid out wider than the column and
  // its right edge — the ``ab`` toggle and the pill's own curve — was clipped
  // away. Both levels have to opt in.
  assert.match(ruleBody('.files-tab-filter {'), /min-width:\s*0/);
  assert.match(ruleBody('.files-tab-filter-field {'), /min-width:\s*0/);
  assert.match(ruleBody('.files-tab-filter-input {'), /min-width:\s*0/);
  // ...and whatever the width, the capsule never paints outside its own box.
  assert.match(ruleBody('.files-tab-filter-field {'), /overflow:\s*hidden/);
});
