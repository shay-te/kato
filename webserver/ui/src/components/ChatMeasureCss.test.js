// Pins the chat pane's width behaviour in the COMPILED css.
//
// Two operator complaints pull in opposite directions and both are right:
//   1. "the text is hard to read"  → prose needs a measure (45-75 chars is
//      the readable range; past ~100 the eye loses the line start).
//   2. "when I widen the chat panel the content does not span the width"
//      → capping the CONTAINER left half a wide pane empty and squeezed
//      tables into a text column with white space beside them.
//
// The resolution is that the measure belongs to PROSE, not to the pane.
// These assertions exist so a future tidy-up cannot collapse the two again.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

// Sass keeps grouped selectors, so a rule may declare several at once
// (``a, b, c { … }``). Match the selector anywhere in the LIST rather than
// only where it immediately precedes the brace.
function ruleBody(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(
    new RegExp(`(?:^|,)\\s*${escaped}\\s*(?:,[^{]*)?\\{([^}]*)\\}`, 'm'),
  );
  assert.ok(match, `expected a rule for ${selector}`);
  return match[1];
}

test('the assistant bubble fills the pane it is given', () => {
  // Capping this is what left a widened panel half empty.
  assert.match(ruleBody('.bubble.assistant .bubble-content'),
    /max-width:\s*none\s*;/);
});

test('prose keeps a readable measure', () => {
  for (const element of ['p', 'ul', 'ol', 'blockquote']) {
    const body = ruleBody(`.bubble.assistant .bubble-content > ${element}`);
    assert.match(body, /max-width:\s*min\(100%,\s*88ch\)\s*;/,
      `${element} lost its measure — long lines become hard to scan`);
  }
});

test('the measure never over-constrains a NARROW pane', () => {
  // min(100%, …) rather than a bare ch value: in a narrow pane the
  // percentage wins, so the text still fits instead of overflowing.
  assert.match(ruleBody('.bubble.assistant .bubble-content > p'),
    /min\(100%/);
});

test('tables and code blocks take the full width', () => {
  // A table squeezed into a text measure wraps every cell — the opposite
  // of what the measure is for.
  for (const element of ['table', 'pre', 'img']) {
    const body = ruleBody(`.bubble.assistant .bubble-content > ${element}`);
    assert.match(body, /width:\s*100%\s*;/,
      `${element} is being constrained to the prose measure`);
  }
});
