// A long toast must get WIDER first and SCROLL second — and show a
// scrollbar only when it actually overflows.
//
// Operator report, from a 25-repo "Pushed (UNA-2742)" report: the card was
// capped at 560px, so nearly every per-repo line ("repo: nothing to push —
// no commits on the task branch and a clean tree") wrapped to two or three,
// and the resulting card ran off the bottom of the screen with no way to
// reach the rest.
//
// Asserted against the COMPILED sheet, not the .scss: sass writes an error
// stub over app.css and still exits 0, so a rule that never compiled would
// otherwise pass a source-level check. See StylesheetBuiltCss.test.js.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

// ANCHORED at a line start — an unanchored search also matches a selector
// that merely ENDS with the wanted one, which silently reads the wrong
// rule's body. Same helper shape as DiffPaneCss.test.js.
function ruleRegex(selector, flags = '') {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?:^|\\n)${escaped}\\s*(?:,[^{]*)?\\{([^}]*)\\}`, flags);
}

function ruleBody(selector) {
  const match = css.match(ruleRegex(selector));
  assert.ok(match, `expected ${selector} rule to exist`);
  return match[1];
}

function assertDeclaration(body, property, value) {
  assert.match(body, new RegExp(`${property}\\s*:\\s*${value}\\s*;`));
}

test('the toast card is wide enough that per-repo lines stop wrapping', () => {
  const body = ruleBody('.toast');
  assertDeclaration(body, 'max-width', '880px');
  // The floor matters too: a one-line toast must not collapse to its text.
  assertDeclaration(body, 'min-width', '320px');
});

test('the container still clamps the card on a narrow window', () => {
  // 880px is wider than a small laptop viewport, so the clamp is what keeps
  // the card on screen rather than hanging off the right edge.
  const body = ruleBody('.toast-container');
  assertDeclaration(body, 'max-width', 'calc\\(100vw - 48px\\)');
});

test('only the MESSAGE scrolls, so the title and close stay reachable', () => {
  const body = ruleBody('.toast-message');
  assertDeclaration(body, 'max-height', '60vh');
  assertDeclaration(body, 'overflow-y', 'auto');
});

test('the scrollbar appears only when the message overflows', () => {
  // ``auto``, never ``scroll``: ``scroll`` reserves a gutter on every toast,
  // so a two-line card would show an empty track that reads as a broken
  // scrollbar. This is the whole "visible only in those cases" requirement.
  const body = ruleBody('.toast-message');
  assert.doesNotMatch(
    body, /overflow-y\s*:\s*scroll/,
    'overflow-y: scroll would reserve a gutter on short toasts',
  );
});

test('the card itself does not scroll — that would hide the close button', () => {
  const body = ruleBody('.toast');
  assert.doesNotMatch(
    body, /overflow-y\s*:\s*(auto|scroll)/,
    'scrolling the card would carry the × and the glyph out of view',
  );
});

test('no second scrollbar theme — the global one styles the bar', () => {
  // The sheet already themes every scrollable element (thin, dark) at the
  // top. A per-toast ::-webkit-scrollbar block would be a divergent copy.
  assert.doesNotMatch(
    css, /\.toast-message::-webkit-scrollbar/,
    'reuse the global scrollbar theme instead of re-declaring it',
  );
});
