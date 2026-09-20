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
  // Asserted as a PAIR reading one variable, not as two hard-coded numbers.
  // The header cancels the section's inset exactly; pinning both literals
  // meant bumping the inset for breathing room silently left the header
  // 4px short of the card edge, and only one of the two assertions caught it.
  assert.match(ruleBody('.files-tab-repo'), /padding-left:\s*var\(--files-card-inset\);/);
  assert.match(ruleBody('.files-tab-repo'), /--files-card-inset:\s*\d+px;/);
  assert.match(
    ruleBody('.files-tab-repo-header'),
    /margin-left:\s*calc\(-1 \* var\(--files-card-inset\)\);/,
  );
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

test('the repo section is rounded on ALL FOUR corners', () => {
  // It was "10px 10px 0 0" — curved at the top, cut off square at the
  // bottom, which is what the operator sent a screenshot of. The tree does
  // scroll inside the card past the height cap, but it cannot reach these
  // corners; see the next test for why.
  assert.match(ruleBody('.files-tab-repo'), /border-radius:\s*10px;/);
});

test('the card insets its children by at least the radius', () => {
  // THE reason the bottom can be round. The arc occupies the outer 10px of
  // each corner; every child — including the scrolling tree — starts
  // --files-card-inset in from the border. While that inset is >= the
  // radius, the arc's footprint is card background and nothing can clip it.
  // Drop the inset below the radius and rows WILL slide under the curve,
  // which is exactly what the old square bottom was guarding against.
  const inset = /--files-card-inset:\s*(\d+)px;/.exec(ruleBody('.files-tab-repo'));
  const radius = /border-radius:\s*(\d+)px;/.exec(ruleBody('.files-tab-repo'));
  assert.ok(inset, 'no --files-card-inset on .files-tab-repo');
  assert.ok(radius, 'no border-radius on .files-tab-repo');
  assert.ok(
    Number(inset[1]) >= Number(radius[1]),
    `inset ${inset[1]}px must be >= radius ${radius[1]}px`,
  );
});

test('the header stays square at the bottom while a tree is under it', () => {
  // The section is round all round; the header is not, because expanded it
  // hands off to the tree. A curve here would cut a notch out of a filled
  // card. Collapsed is the exception and has its own rule.
  assert.match(ruleBody('.files-tab-repo-header'), /border-radius:[^;]*0 0;/);
});

test('collapsed, the header closes with a curve instead', () => {
  // Nothing under it then — the header IS the card, so its bottom corners
  // have to match the section's.
  assert.match(
    ruleBody('.files-tab-repo.is-collapsed .files-tab-repo-header'),
    /border-radius:\s*10px;/,
  );
});

test('the header keeps its own background so rows never show through it', () => {
  // Clipping stops content OUTSIDE the curve; an opaque header is what
  // hides the rows sliding under it.
  assert.match(ruleBody('.files-tab-repo-header'), /background:\s*#/);
});
