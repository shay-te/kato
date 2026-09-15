// The files pane's header and its tree container share ONE horizontal inset.
//
// "for the tree container make its spacing left right same as the toolbar."
// The header (search box + toolbar) was inset 6px from the pane edge while the
// tree container ran flush to it, so the repo cards stuck out past the toolbar
// on both sides — and a comment on the header claimed the two already matched.
//
// The inset on the tree container has to be MARGIN, not padding: the scroller
// clips to its own rounded corners, and that clip only hides rows scrolling
// behind the pinned repo header when the sections sit flush against it.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const CSS = readFileSync(
  join(new URL('../../..', import.meta.url).pathname, 'static/css/app.css'),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

// Declarations of the FIRST rule whose selector list contains ``selector``
// exactly (as a whole selector, not a prefix of another class).
function declarationsFor(selector) {
  const rule = /([^{}]+)\{([^{}]*)\}/g;
  let m = rule.exec(CSS);
  while (m !== null) {
    const selectors = m[1].split(',').map((part) => part.trim());
    if (selectors.includes(selector)) {
      const decls = {};
      for (const line of m[2].split(';')) {
        const idx = line.indexOf(':');
        if (idx > 0) { decls[line.slice(0, idx).trim()] = line.slice(idx + 1).trim(); }
      }
      if (Object.keys(decls).length) { return decls; }
    }
    m = rule.exec(CSS);
  }
  return null;
}

// Horizontal (right, left) from a padding/margin shorthand.
function horizontal(shorthand) {
  const parts = String(shorthand).trim().split(/\s+/);
  if (parts.length === 1) { return [parts[0], parts[0]]; }
  if (parts.length === 2 || parts.length === 3) { return [parts[1], parts[1]]; }
  return [parts[1], parts[3]];
}

test('the header has a horizontal inset', () => {
  const header = declarationsFor('.files-tab-header');
  assert.ok(header && header.padding, '.files-tab-header has no padding');
  const [right, left] = horizontal(header.padding);
  assert.notEqual(left, '0', 'the header is flush with the pane edge');
  assert.equal(left, right);
});

test('the tree container is inset exactly as far as the toolbar', () => {
  const [headerRight, headerLeft] = horizontal(declarationsFor('.files-tab-header').padding);
  const body = declarationsFor('.files-tab-body');
  assert.ok(body, '.files-tab-body rule not found');
  assert.equal(body['margin-left'], headerLeft,
    'the tree container\'s left edge no longer lines up with the toolbar');
  assert.equal(body['margin-right'], headerRight,
    'the tree container\'s right edge no longer lines up with the toolbar');
});

test('the inset is margin, so the sections stay flush with the rounded clip', () => {
  const body = declarationsFor('.files-tab-body');
  assert.equal(body['padding-left'], '0',
    'padding on the scroller insets the sections away from its rounded clip');
  assert.equal(body['padding-right'], '0',
    'padding on the scroller insets the sections away from its rounded clip');
});
