// The tab pill's pin / × buttons are 16px discs sitting in a ~30px-tall
// pill whose <li> selects the task on click. Without an expanded hit
// target, the 6px bands of padding above and below each disc belong to the
// <li> — so a click that missed the × by a couple of pixels opened the task
// instead of forgetting it. The event wiring was never at fault
// (handleForget stops propagation); the click simply never reached the
// button. These pin the geometry that makes the button catch it.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

function ruleBody(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `expected ${selector} rule to exist`);
  return match[1];
}

function assertDeclaration(body, property, value) {
  assert.match(body, new RegExp(`${property}\\s*:\\s*${value}\\s*;`));
}

test('pencil, pin and × stretch their hit target to the pill edges', () => {
  const body = ruleBody(
    '.tabs-pane-side .tab .tab-rename-btn::before,\n'
    + '.tabs-pane-side .tab .tab-pin-btn::before,\n'
    + '.tabs-pane-side .tab .tab-forget-btn::before',
  );
  assertDeclaration(body, 'position', 'absolute');
  // Vertical inset matches .tab's declared 6px padding exactly: the target
  // reaches the pill's padding edge and stops. `.tab` has no
  // `overflow: hidden`, so anything larger would bleed an invisible click
  // target into the gap between tabs.
  assertDeclaration(body, 'top', '-6px');
  assertDeclaration(body, 'bottom', '-6px');
  // Horizontal inset stays under the 6px inter-child margin so the two
  // discs' targets never overlap each other.
  assertDeclaration(body, 'left', '-3px');
  assertDeclaration(body, 'right', '-3px');
});

test('the × keeps its 16px disc — only the hit area grew', () => {
  const body = ruleBody('.tabs-pane-side .tab .tab-forget-btn');
  assertDeclaration(body, 'width', '16px');
  assertDeclaration(body, 'height', '16px');
  // Hidden until the tab is hovered/active. visibility:hidden also hides
  // the ::before, so a resting tab has no invisible click-catcher on it.
  assertDeclaration(body, 'visibility', 'hidden');
});

test('the pencil is revealed on hover, like the pin and ×', () => {
  // Hidden at rest so a strip of tabs stays readable; visible the moment the
  // pointer is on the tab, which is what makes renaming discoverable at all.
  assertDeclaration(
    ruleBody('.tabs-pane-side .tab .tab-rename-btn'), 'visibility', 'hidden',
  );
  assertDeclaration(
    ruleBody(
      '.tabs-pane-side .tab:hover .tab-rename-btn,\n'
      + '.tabs-pane-side .tab.active .tab-rename-btn',
    ),
    'visibility',
    'visible',
  );
});

test('no reserve survives for the removed changes indicator', () => {
  // The orange commit glyph on the task tab is gone (it read as a stray
  // symbol, and unpushed work is already surfaced by the Push button and the
  // forget-task warning). Its 96px label reserve must go with it, or every
  // tab that used to carry it keeps paying 16px it no longer needs.
  assert.doesNotMatch(css, /has-changes/);
  assert.doesNotMatch(css, /tab-changes-indicator/);
});

test('the name is the part of a row that gives way', () => {
  // A row in the tasks menu is as wide as the menu. The label takes what the
  // dot and the buttons leave and ellipsises a long name, instead of pushing
  // the pencil / pin / × past the row's edge.
  const body = ruleBody('.tabs-pane-side .tab .tab-label');
  assertDeclaration(body, 'flex', '1');
  assertDeclaration(body, 'min-width', '0');
  assertDeclaration(ruleBody('.tabs-pane-side .tab'), 'display', 'flex');
  // The label is a row too, so the name is its own box: inline after the id,
  // "B-2" + "admin admin" read as one word and capitalize skipped "admin".
  assertDeclaration(body, 'display', 'flex');
  const title = ruleBody('.tabs-pane-side .tab .tab-label-title');
  assertDeclaration(title, 'min-width', '0');
  assertDeclaration(title, 'text-overflow', 'ellipsis');
});

test('the rename box is sized in JS, not by a percentage reserve', () => {
  // The input gets an explicit pixel width measured off the label it
  // replaces (Tab.jsx → renameBoxWidth). A `calc(100% - Npx)` reserve here
  // let the box run on under the pin and × — which paint above it, so the
  // overflow was invisible rather than obviously wrong. `max-width: 100%`
  // is the only width rule left, as a floor for the unmeasured case.
  const body = ruleBody('.tabs-pane-side .tab .tab-label-rename');
  assertDeclaration(body, 'max-width', '100%');
  assert.doesNotMatch(body, /(^|[^-])width:\s*100%/);
  assert.doesNotMatch(css, /\.tab-label\.is-renaming \{[^}]*max-width/);
  // And the label must not clip it: a box flush against its container's edge
  // loses its 1px border to sub-pixel accumulation however exact the width.
  assertDeclaration(
    ruleBody('.tabs-pane-side .tab .tab-label.is-renaming'),
    'overflow',
    'visible',
  );
});

test('the rename box gets the label to itself', () => {
  // Sharing the label's ~158px with the id left roughly 90px of a 160px
  // input visible and clipped the rest, so the operator could not see the
  // text they were editing — and retyping it appended to the hidden value.
  assertDeclaration(
    ruleBody('.tabs-pane-side .tab .tab-label.is-renaming .tab-label-id'),
    'display',
    'none',
  );
  assertDeclaration(
    ruleBody('.tabs-pane-side .tab .tab-label-rename'), 'width', '100%',
  );
});

test('the × target does not claim the gap to the pin', () => {
  // Matched against the whole sheet, not via ruleBody: the shared hit-area
  // rule also ENDS in this selector, so a plain lookup finds that one first.
  // This pins the standalone override that follows it. Expanded leftward it
  // would claim the gap to the pin (both targets meet there, and the × wins),
  // turning the destructive red on while the operator aims at the pin.
  assert.match(css, /\.tab-forget-btn::before \{\s*left: 0;\s*right: 0;\s*\}/);
});

test('the buttons paint above the row', () => {
  // Their stretched hit areas (::before) overlap the row around them; above
  // it, a press on the edge of a disc reaches the button, not the row (which
  // would select the task instead).
  assertDeclaration(
    ruleBody(
      '.tabs-pane-side .tab .tab-rename-btn,\n'
      + '.tabs-pane-side .tab .tab-pin-btn,\n'
      + '.tabs-pane-side .tab .tab-forget-btn',
    ),
    'z-index',
    '2',
  );
  assert.doesNotMatch(css, /tab-resize-handle/);
});
