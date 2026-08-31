// The maximized chat must actually reach the window edge.
//
// "Maximized" collapses the files and editor columns to ZERO WIDTH rather
// than unmounting them — deliberately, because a zero-width column keeps
// Monaco alive with its scroll position, folds and find widget intact, and
// tearing that down to rebuild it a moment later is the "UI shifts while you
// are reading it" behaviour this codebase has rules against.
//
// But zero-width is not invisible. Each collapsed card still painted its 1px
// left and right borders, and the grid still opened its 8px gutter either
// side, so the maximized chat sat behind two hairlines and a band of dead
// space — reported as "no need for this two lines on the left".
//
// Asserted against the COMPILED css, not the scss: the bug was in what the
// browser received, and a nested/overridden rule can compile to something
// other than what the source appears to say.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const CSS = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

function ruleBodies(selector) {
  const bodies = [];
  let from = 0;
  for (;;) {
    const at = CSS.indexOf(selector, from);
    if (at === -1) { return bodies; }
    const open = CSS.indexOf('{', at);
    const close = CSS.indexOf('}', open);
    if (open === -1 || close === -1) { return bodies; }
    bodies.push(CSS.slice(open + 1, close));
    from = close;
  }
}

test('the maximized chat keeps the collapsed columns at zero width', () => {
  const bodies = ruleBodies('#layout.has-top-tabs.is-chat-maximized {');
  assert.ok(bodies.length > 0, 'no maximized layout rule emitted');
  assert.match(bodies[0], /grid-template-columns:\s*0 0 minmax\(0, 1fr\)/);
});

test('it closes the gutter that held the chat off the left edge', () => {
  const body = ruleBodies('#layout.has-top-tabs.is-chat-maximized {')[0];
  assert.match(body, /column-gap:\s*0/);
  assert.match(body, /padding-left:\s*0/);
});

test('the two collapsed panes paint no border', () => {
  // A card at zero width is nothing BUT border — the two hairlines.
  const bodies = ruleBodies('#layout.has-top-tabs.is-chat-maximized > #center-pane');
  assert.ok(bodies.length > 0, 'no border reset for the collapsed panes');
  assert.match(bodies[bodies.length - 1], /border-width:\s*0/);
  assert.ok(
    CSS.includes('#layout.has-top-tabs.is-chat-maximized > #right-pane'),
    'the files column is still painting its border',
  );
});

test('the resizers stay hidden while maximized', () => {
  // Nothing to resize against, and a live drag would rewrite the stored
  // widths the restore depends on.
  const bodies = ruleBodies('#layout.is-chat-maximized .pane-resizer');
  assert.ok(bodies.length > 0);
  assert.match(bodies[0], /display:\s*none/);
});
