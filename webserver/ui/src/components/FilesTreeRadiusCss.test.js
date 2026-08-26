// The file tree's top corners are rounded, and STAY rounded while scrolling.
//
// The curve cannot live on ``.files-tab-repo``: a rounded top there floats
// over square rows the moment its header pins mid-scroll (see the rationale
// on that rule). And there is no ``.panel-card`` wrapping this pane to
// inherit one from — so the tree met the toolbar as a hard square edge.
//
// It goes on the SCROLLER, which clips its content to its own radius at every
// scroll offset.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const css = readFileSync(
  join(new URL('..', import.meta.url).pathname, '../../static/css/app.css'),
  'utf8',
);

function ruleBody(selector) {
  const start = css.indexOf(`\n${selector} {`);
  assert.notEqual(start, -1, `no rule for ${selector}`);
  return css.slice(start, css.indexOf('}', start));
}

test('the SCROLLER owns the clip, so stickiness survives', () => {
  // Clipping the section would round the corner too, but it makes the
  // section a scrollport and its header stops pinning across the tree. The
  // scroller is ALREADY the scrollport, so clipping here costs nothing.
  const body = ruleBody('.files-tab-body');
  assert.match(body, /border-radius:\s*10px 10px 0 0;/);
  assert.match(body, /overflow-y:\s*auto;/);
});

test('the scroller is flush, or the clip never reaches the corners', () => {
  // Horizontal padding here insets every section away from the rounded
  // corners; the clip then never touches them. The inset moved onto the
  // sections, and the header cancels it so it still spans the full width.
  const body = ruleBody('.files-tab-body');
  assert.match(body, /padding-left:\s*0;/);
  assert.match(body, /padding-right:\s*0;/);
  assert.match(ruleBody('.files-tab-repo'), /padding-left:\s*6px;/);
  assert.match(ruleBody('.files-tab-repo-header'), /margin-left:\s*-6px;/);
});

test('the section itself is NOT clipped — that would kill the sticky header', () => {
  assert.match(ruleBody('.files-tab-repo'), /overflow:\s*visible;/);
});

test('the border is back, and the header matches its radius', () => {
  assert.match(ruleBody('.files-tab-repo'), /border:\s*1px solid/);
  assert.match(
    ruleBody('.files-tab-repo-header'), /border-radius:\s*10px 10px 0 0;/,
  );
});

test('the repo section is rounded on top', () => {
  assert.match(
    ruleBody('.files-tab-repo'), /border-radius:\s*10px 10px 0 0;/,
  );
});

test('both stay square at the BOTTOM', () => {
  // The tree scrolls past the bottom with nothing clipping it, so a rounded
  // bottom would have rows sliding under a curve. Both radii end in "0 0".
  for (const sel of ['.files-tab-repo', '.files-tab-repo-header']) {
    assert.match(ruleBody(sel), /border-radius:[^;]*0 0;/, sel);
  }
});

test('the header keeps its own background so rows never show through it', () => {
  // Clipping stops content OUTSIDE the curve; an opaque header is what
  // hides the rows sliding under it.
  assert.match(ruleBody('.files-tab-repo-header'), /background:\s*#/);
});
