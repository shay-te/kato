// The file tree's readable measure, pinned in the COMPILED css.
//
// The rows are ``width: 100%`` with their ``+N −N`` badges pushed to the far
// edge by ``margin-left: auto``. On a wide pane that leaves a stretch of dead
// space between every filename and its own numbers, and the column reads as
// two unrelated lists: "the files changes tree is 100% width in the
// container. it's not looking good."
//
// Compiled CSS, not the source: a Sass edit that fails to reach the bundle is
// exactly the failure this guards against.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const CSS = readFileSync(
  join(new URL('../../..', import.meta.url).pathname, 'static/css/app.css'),
  'utf8',
);

function ruleFor(selector) {
  const at = CSS.indexOf(`\n${selector} {`);
  assert.notEqual(at, -1, `missing rule: ${selector}`);
  return CSS.slice(at, CSS.indexOf('}', at));
}

test('the tree rows and the repo header share ONE measure', () => {
  // Both, or neither. Capping only the rows would leave the header's own
  // "+34 −4" at the far edge while every file's sat at the cap — two
  // misaligned columns, which is worse than one distant one.
  const rule = ruleFor('.files-tab-repo-header-inner,\n.files-tab-repo .diff-file-tree-row');
  assert.match(rule, /max-width:\s*min\(100%,\s*var\(--files-measure\)\)/);
});

test('the measure is defined on the repo section', () => {
  assert.match(ruleFor('.files-tab-repo'), /--files-measure:\s*\d+px/);
});

test('a narrow pane is unaffected', () => {
  // ``min(100%, …)`` — the cap only bites once there is more width than the
  // content can use. A bare ``max-width`` would force horizontal overflow on
  // a pane narrower than the measure.
  const rule = ruleFor('.files-tab-repo-header-inner,\n.files-tab-repo .diff-file-tree-row');
  assert.ok(rule.includes('min(100%'), 'measure must be clamped by 100%');
});

test('the header element itself stays full width', () => {
  // Its sticky gradient + blur have to cover the card edge to edge; only the
  // CONTENT row is capped.
  const header = ruleFor('.files-tab-repo-header');
  assert.doesNotMatch(header, /max-width/);
  assert.match(ruleFor('.files-tab-repo-header-inner'), /width:\s*100%/);
});
