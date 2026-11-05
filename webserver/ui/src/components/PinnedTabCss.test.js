// Pinned tabs must NOT be sticky.
//
// They used to be ``position: sticky; left: var(--sticky-left)``, held against
// the strip's left edge while everything else scrolled underneath. That
// premise fails the moment the pinned cluster is wider than the strip —
// there is nowhere left to hold them — so they piled up and painted over one
// another. Reported as "the tasks got below eachother, they dont scroll like
// normal tabs".
//
// Asserted against the COMPILED stylesheet, and deliberately so: the vitest
// tier loads no CSS at all, so the component tests are structurally blind to
// this. They assert only that no INLINE offsets are written — which stays
// true even if ``position: sticky`` were restored in the sheet, meaning the
// original bug could come straight back with every one of them green.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Comments stripped first. They are not styling, and the tombstone comments
// left where the sticky rules used to be quote the very declarations these
// tests assert are absent — matching them would make the tests fail on the
// explanation rather than on the code.
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

test('a pinned tab is not stuck to the strip edge', () => {
  const body = ruleBody('.tabs-pane-top .tab.is-pinned {');
  assert.doesNotMatch(body, /position:\s*sticky/);
  assert.doesNotMatch(body, /left:/);
});

test('the sticky-left custom property is gone from the sheet entirely', () => {
  // The offsets it carried were published by a layout effect that no longer
  // exists; a rule still reading it would silently resolve to the fallback.
  assert.doesNotMatch(css, /--sticky-left/);
});

test('pinned tabs keep their opaque panel', () => {
  // Redundant now that nothing scrolls underneath them, but it is also what
  // makes a pinned tab legible against the strip's gradient.
  const body = ruleBody('.tabs-pane-top .tab.is-pinned {');
  assert.match(body, /background:/);
});

test('the parent of the open sub-task is violet, and a pin cannot hide it', () => {
  // Its first mark was a faint cyan placed BEFORE the pinned rule, so a
  // pinned parent showed nothing; and cyan is the selected tab's colour.
  const selector = '.tabs-pane-top .tab.is-pinned.has-open-subtask {';
  const body = ruleBody(selector);
  assert.match(body, /border-color:\s*#a78bfa/);
  assert.doesNotMatch(body, /#2997ff|#0a84ff/);  // never the selected tab's cyan
  assert.ok(
    css.indexOf(selector) > css.indexOf('.tabs-pane-top .tab.is-pinned {'),
    'the parent mark must come after the pinned rule, or a pinned parent shows nothing',
  );
});

test('the drag affordances are emitted', () => {
  // Without these a reorder gives no feedback at all: no indication of what
  // is moving, nor of where it will land.
  assert.match(ruleBody('.tabs-pane-top .tab.dragging {'), /opacity:/);
  assert.match(ruleBody('.tabs-pane-top .tab.drop-target {'), /outline:/);
});
