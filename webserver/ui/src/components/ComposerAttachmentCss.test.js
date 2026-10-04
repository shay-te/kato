// An attached image has to be recognisable in the composer.
//
// The operator's report was "attaching an image shows just a white square".
// The data URL was provably correct, so the fault was presentation: the 56px
// tile used ``object-fit: cover``, which scales the image to FILL the tile and
// crops the overflow. On a wide screenshot that leaves a centre sliver — often
// an empty region — which reads as a blank square rather than as a crop.
//
// ``contain`` shows the whole frame instead. Guarded here because nothing in
// the JSX tests can see a computed style, so a revert to ``cover`` would pass
// every other gate silently.

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

test('the thumbnail shows the whole image, not a centre crop', () => {
  const body = ruleBody('.message-attachment img');
  assert.match(body, /object-fit:\s*contain;/);
  assert.doesNotMatch(
    body,
    /object-fit:\s*cover;/,
    'cover crops a wide screenshot to a sliver — that is the "white square" bug.',
  );
});

test('the tile is big enough to be worth looking at', () => {
  // 56px was too small to identify a screenshot by.
  const body = ruleBody('.message-attachment');
  const width = /width:\s*(\d+)px;/.exec(body);
  assert.ok(width, 'no explicit width on the attachment tile');
  assert.ok(
    Number(width[1]) >= 72,
    `attachment tile is ${width[1]}px — too small to recognise an image in.`,
  );
});

test('the thumbnail advertises that it opens', () => {
  // The affordance IS the fix for "I cannot see what I attached".
  assert.match(ruleBody('.message-attachment-open'), /cursor:\s*zoom-in;/);
});

test('the full preview scales down instead of overflowing the modal', () => {
  const body = ruleBody('.image-preview-full');
  assert.match(body, /max-width:\s*100%;/);
  assert.match(body, /max-height:\s*\d+vh;/);
});
