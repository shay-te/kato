// A settings subsection heading must look like a heading.
//
// The Chat panel's three <legend>s were normal weight in the muted HINT
// colour — the same treatment as the option hints stacked under them — so
// they read as body copy rather than as titles over the groups they head:
// "3 sections here, and the title for each section looks like plain text
// with no padding from bottom".
//
// The field-group title in the schema panels already had the right look, so
// the fix was to share one declaration rather than to hand-tune a second.
// These tests pin that they stay shared; a future edit to one of them that
// does not touch the others fails here.
//
// Asserted against the COMPILED stylesheet — the vitest tier loads no CSS,
// so it is structurally blind to this, and a later declaration can silently
// cancel an earlier one.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

// The heading selectors that must share one look.
const TITLES = [
  '.settings-section-title',
  '.chat-settings-legend',
  '.settings-drawer-field-group-title',
];

function declarationsFor(selector) {
  // Collect every rule whose selector list contains this exact selector, so
  // grouped rules (the shared declaration) are picked up along with the
  // single-selector overrides that follow them.
  //
  // Anchoring each match on the PREVIOUS rule's closing brace does not work:
  // the match consumes that brace, so the very next rule has nothing left to
  // anchor on and is skipped. Two adjacent rules for the same selector — the
  // exact shape used here — then look like one. Match rule bodies directly
  // instead and filter by selector afterwards.
  const found = [];
  const pattern = /([^{}]+)\{([^{}]*)\}/g;
  let match = pattern.exec(css);
  while (match !== null) {
    const selectors = match[1].split(',').map((s) => s.trim());
    if (selectors.includes(selector)) found.push(match[2]);
    match = pattern.exec(css);
  }
  return found.join(';');
}

for (const selector of TITLES) {
  test(`${selector} is emitted at all`, () => {
    assert.notEqual(declarationsFor(selector), '', `no rule for ${selector}`);
  });

  test(`${selector} reads as a heading, not as body copy`, () => {
    const body = declarationsFor(selector);
    // Weight is what separated a heading from the hints beneath it.
    assert.match(body, /font-weight:\s*600/, `${selector} is not bold`);
  });

  test(`${selector} uses the panel text colour, not the muted hint colour`, () => {
    // The legend's original colour was the same token the hints use, which
    // is the other half of why it disappeared into them.
    const body = declarationsFor(selector);
    assert.match(body, /color:\s*#/, `${selector} sets no colour`);
  });

  test(`${selector} has space beneath it`, () => {
    // "no padding from bottom" — the heading sat on top of the first card.
    const body = declarationsFor(selector);
    const margin = /margin:\s*0\s+0\s+(\d+)px|margin-bottom:\s*(\d+)px/.exec(body);
    assert.ok(margin, `${selector} sets no bottom margin`);
    const px = Number(margin[1] || margin[2]);
    assert.ok(px >= 4, `${selector} bottom margin is only ${px}px`);
  });
}

test('the three headings share one colour and one size', () => {
  // The point of the shared declaration. If someone re-forks one of these,
  // the values drift and this fails.
  const read = (selector, property) => {
    const hits = [...declarationsFor(selector).matchAll(
      new RegExp(`${property}:\\s*([^;]+)`, 'g'),
    )];
    return hits.length ? hits[hits.length - 1][1].trim() : null;
  };
  const colours = new Set(TITLES.map((s) => read(s, 'color')));
  const sizes = new Set(TITLES.map((s) => read(s, 'font-size')));
  assert.equal(colours.size, 1, `colours drifted: ${[...colours].join(' vs ')}`);
  assert.equal(sizes.size, 1, `sizes drifted: ${[...sizes].join(' vs ')}`);
});

test('sections are separated from the group above them', () => {
  // Without this the previous section's last card butts into the next
  // heading and three sections read as one long list.
  assert.match(
    declarationsFor('.settings-drawer-section-head'),
    /margin-top:\s*\d+px/,
  );
});
