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

test('the maximized chat keeps the files rail and collapses the preview to zero width', () => {
  // The files tree stays as the slim rail (2026-11-18: "when expanding the
  // chat make sure to have the file tree collapse on the side, same as view
  // file full screen"); only the preview column goes to zero.
  const bodies = ruleBodies('#layout.has-top-tabs.is-chat-maximized {');
  assert.ok(bodies.length > 0, 'no maximized layout rule emitted');
  assert.match(bodies[0], /grid-template-columns:\s*calc\(28px \+ 8px\) 0 minmax\(0, 1fr\)/);
});

test('it closes the column gap that held the chat off-centre', () => {
  const body = ruleBodies('#layout.has-top-tabs.is-chat-maximized {')[0];
  assert.match(body, /column-gap:\s*0/);
});

test('the left inset matches the right one', () => {
  // The shell's own horizontal padding is what makes the two sides equal, so
  // the maximized rule must NOT override one side of it. An earlier attempt
  // set ``padding-left: 0``, which pinned the chat flush to the window edge
  // while the right kept its gutter.
  const body = ruleBodies('#layout.has-top-tabs.is-chat-maximized {')[0];
  assert.doesNotMatch(body, /padding-left\s*:/);
  assert.doesNotMatch(body, /padding-inline-start\s*:/);
  // ...and the padding it inherits is symmetric.
  const shell = ruleBodies('#layout.has-top-tabs {')[0];
  const padding = shell.match(/[^-]padding:\s*([^;]+);/);
  assert.ok(padding, 'the shell no longer sets a padding to inherit');
  const parts = padding[1].trim().split(/\s+/);
  assert.equal(parts.length, 3, `expected "top x-axis bottom", got ${padding[1]}`);
});

test('the collapsed preview paints no border; the files rail keeps its card', () => {
  // A card at zero width is nothing BUT border — the hairlines.
  const bodies = ruleBodies('#layout.has-top-tabs.is-chat-maximized > #center-pane');
  assert.ok(bodies.length > 0, 'no border reset for the collapsed preview');
  assert.match(bodies[bodies.length - 1], /border-width:\s*0/);
  assert.ok(!CSS.includes('#layout.has-top-tabs.is-chat-maximized > #right-pane'));
});

test('the resizers stay hidden while maximized', () => {
  // Nothing to resize against, and a live drag would rewrite the stored
  // widths the restore depends on.
  const bodies = ruleBodies('#layout.has-files-rail .pane-resizer');
  assert.ok(bodies.length > 0);
  assert.match(bodies[0], /display:\s*none/);
});

// The centre (file / diff view) maximizes the same way: zero-width tree and
// chat columns (still mounted), no gap, no borders, no resizers.
test('the maximized centre keeps a slim column for the files rail and collapses the chat', () => {
  const bodies = ruleBodies('#layout.has-top-tabs.is-center-maximized {');
  assert.ok(bodies.length > 0, 'no maximized-centre layout rule emitted');
  assert.match(bodies[0], /grid-template-columns:\s*calc\(28px \+ 8px\) minmax\(0, 1fr\) 0/);
  assert.match(bodies[0], /column-gap:\s*0/);
  assert.doesNotMatch(bodies[0], /padding/);
});

test('the maximized centre\'s chat paints no border, and nothing resizes', () => {
  const borders = ruleBodies('#layout.has-top-tabs.is-center-maximized > #session-pane');
  assert.ok(borders.length > 0, 'the chat column is still painting its border');
  assert.match(borders[borders.length - 1], /border-width:\s*0/);
});

// The files tree beside a maximized centre: a slim rail that expands OVER the
// code while pointed at, its tree kept at its own width (nothing reflows).
test('the files rail is slim, lies above the code, and keeps its tree at full width', () => {
  const rail = ruleBodies('#layout.has-top-tabs.has-files-rail > #right-pane {')[0];
  assert.match(rail, /width:\s*28px/);
  assert.match(rail, /overflow:\s*hidden/);
  assert.match(rail, /z-index:\s*\d+/);
  const tree = ruleBodies('#layout.has-top-tabs.has-files-rail > #right-pane > #right-pane-root {')[0];
  assert.match(tree, /width:\s*calc\(var\(--left-pane-width, 320px\) - 2px\)/);
});

test('hovered open it takes the tree\'s own width; collapsed it hides the tree behind a "Files" label', () => {
  const open = ruleBodies('#layout.has-top-tabs.has-files-rail > #right-pane.is-rail-open {')[0];
  assert.match(open, /width:\s*var\(--left-pane-width, 320px\)/);
  const hidden = ruleBodies('#layout.has-top-tabs.has-files-rail > #right-pane:not(.is-rail-open) > #right-pane-root {')[0];
  assert.match(hidden, /visibility:\s*hidden/);
  const label = ruleBodies('#layout.has-top-tabs.has-files-rail > #right-pane:not(.is-rail-open)::after {')[0];
  assert.match(label, /content:\s*"Files"|content:\s*'Files'/);
});
