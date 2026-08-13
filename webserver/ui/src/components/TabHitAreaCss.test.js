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

test('pin and × stretch their hit target to the pill edges', () => {
  const body = ruleBody(
    '.tabs-pane-top .tab .tab-pin-btn::before,\n.tabs-pane-top .tab .tab-forget-btn::before',
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
  const body = ruleBody('.tabs-pane-top .tab .tab-forget-btn');
  assertDeclaration(body, 'width', '16px');
  assertDeclaration(body, 'height', '16px');
  // Hidden until the tab is hovered/active. visibility:hidden also hides
  // the ::before, so a resting tab has no invisible click-catcher on it.
  assertDeclaration(body, 'visibility', 'hidden');
});

test('the buttons stack above the resize handle', () => {
  // .tab-resize-handle::after is a circle as wide as the tab is tall,
  // clipped to its right half. clip-path culls hit-testing but opacity:0
  // does not, and the handle is positioned + last in DOM order — so
  // without these it took the clicks aimed at the × beside it.
  assertDeclaration(
    ruleBody(
      '.tabs-pane-top .tab .tab-pin-btn,\n.tabs-pane-top .tab .tab-forget-btn',
    ),
    'z-index',
    '2',
  );
  assertDeclaration(
    ruleBody('.tabs-pane-top .tab .tab-resize-handle'), 'z-index', '1',
  );
});
