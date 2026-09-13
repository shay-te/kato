// The diff gutter's "+" comment badge — one rule, and the ORDER that makes it
// correct on deleted lines.
//
// The badge used to be written out twice, byte for byte: once for the
// new-side gutter and once for the old-side gutter of a delete line. Grouping
// them is only safe in one direction. Between the two sat the delete-line
// suppression rule, which has the SAME specificity (0,4,1) as the new-side
// badge and wins purely by coming later in the file. Group the badge at the
// lower position and it moves after the suppression, wins, and every deleted
// line grows a second "+".
//
// So this pins the relationship, not the declarations: exactly one badge
// block, and the suppression after it.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const CSS = readFileSync(
  join(new URL('../../..', import.meta.url).pathname, 'static/css/app.css'),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

const NEW_SIDE_BADGE = '.diff-line:hover .diff-gutter + .diff-gutter::after';
const OLD_SIDE_BADGE = '.diff-line:hover .diff-gutter-delete:first-child::after';
const SUPPRESSION = '.diff-line:hover .diff-gutter-delete + .diff-gutter-delete::after';

function countOf(needle) {
  return CSS.split(needle).length - 1;
}

test('the badge is declared once, for both gutters', () => {
  assert.equal(countOf(NEW_SIDE_BADGE), 1, 'the new-side badge selector appears more than once');
  assert.equal(countOf(OLD_SIDE_BADGE), 1, 'the old-side badge was split back out into its own copy');
  // Scoped to the diff gutter. The Monaco add-comment glyph
  // (``.kato-add-comment-glyph::before``) also draws a "+", and it is not this
  // badge — a whole-file count flagged it as a duplicate that was never there.
  const gutterBadges = [...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, selectors, body]) => (
      selectors.includes('diff-gutter') && /content:\s*"\+"/.test(body)
    ));
  assert.equal(gutterBadges.length, 1, 'a second "+" badge body exists in the diff gutter');
});

test('both badge selectors sit on the same rule', () => {
  const at = CSS.indexOf(NEW_SIDE_BADGE);
  const openBrace = CSS.indexOf('{', at);
  const selectorList = CSS.slice(at, openBrace);
  assert.ok(
    selectorList.includes(OLD_SIDE_BADGE),
    'the two gutters no longer share one badge rule',
  );
});

test('the delete-line suppression comes AFTER the badge — same specificity, order decides', () => {
  const badge = CSS.indexOf(NEW_SIDE_BADGE);
  const suppression = CSS.indexOf(SUPPRESSION);
  assert.ok(badge >= 0 && suppression >= 0, 'a gutter badge rule is missing');
  assert.ok(
    suppression > badge,
    'the suppression moved above the badge rule, so the badge now wins and '
    + 'deleted lines show two "+" badges',
  );
});
