import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

function ruleBody(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `expected ${selector} rule to exist`);
  return match[1];
}

function assertDeclaration(body, property, value) {
  const declaration = new RegExp(`${property}\\s*:\\s*${value}\\s*;`);
  assert.match(body, declaration);
}

test('EventLog sticky prompts wrap instead of truncating to one line', () => {
  const textBody = ruleBody('.chat-sticky-prompt-text');

  assertDeclaration(textBody, 'white-space', 'pre-wrap');
  assertDeclaration(textBody, 'overflow-wrap', 'anywhere');
  assert.doesNotMatch(textBody, /text-overflow\s*:\s*ellipsis\s*;/);
  assert.doesNotMatch(textBody, /white-space\s*:\s*nowrap\s*;/);
});

test('EventLog prompt keeps sticky positioning from the shared sticky header', () => {
  const sharedBody = ruleBody('.sticky-section-header');
  const promptBody = ruleBody('.chat-sticky-prompt');

  assertDeclaration(sharedBody, 'position', 'sticky');
  assert.doesNotMatch(promptBody, /position\s*:\s*relative\s*;/);
});

test('EventLog prompt is visually distinct from ordinary chat bubbles', () => {
  const promptBody = ruleBody('.chat-sticky-prompt');
  const labelBody = ruleBody('.chat-sticky-prompt-label');
  assert.match(promptBody, /background\s*:\s*linear-gradient\(/);
  assertDeclaration(promptBody, 'border-top', '1px solid rgba\\(10, 132, 255, 0\\.4\\)');
  assertDeclaration(promptBody, 'border-bottom', '1px solid rgba\\(10, 132, 255, 0\\.4\\)');
  assertDeclaration(labelBody, 'color', '#cce0ff');
  // The gradient + hairlines ARE the distinction. No left accent bar:
  // against the cyan fill it read as a doubled blue edge.
  assert.doesNotMatch(css, /\.chat-sticky-prompt-toggle::before\s*\{/);
});

test('EventLog sticky prompts collapse to three lines with snippet expand button', () => {
  const wrapBody = ruleBody('.chat-sticky-prompt-text-wrap.is-collapsed');
  const expandBody = ruleBody('.chat-sticky-prompt-expand');
  // The fade spans the FULL prompt box, so it lives on the full-width
  // toggle (under the "You asked" label too), not just the text column.
  const fadeBody = ruleBody(
    '.chat-sticky-prompt.is-collapsible:not(.is-expanded) .chat-sticky-prompt-toggle::after',
  );

  // 3 lines x 1.5 line-height x 12.5px font; Sass evaluates the static
  // calc() at compile time to its exact equivalent, 56.25px.
  assertDeclaration(wrapBody, 'max-height', '56\\.25px');
  assertDeclaration(wrapBody, 'overflow', 'hidden');
  assertDeclaration(expandBody, 'bottom', '0');
  assert.match(fadeBody, /background\s*:\s*linear-gradient\(/);
  // Full-width: the fade pins to both edges of the prompt box.
  assertDeclaration(fadeBody, 'left', '0');
  assertDeclaration(fadeBody, 'right', '0');
});

test('expanded prompt pins the collapse toggle to the TOP, not the bottom', () => {
  // The prompt bar is a sticky header, so while pinned only its top shows —
  // a bottom-anchored collapse button is unreachable without scrolling the
  // long prompt out of view. Expanded, the toggle moves to the top.
  const expandedToggle = ruleBody(
    '.chat-sticky-prompt.is-expanded .chat-sticky-prompt-expand',
  );
  assertDeclaration(expandedToggle, 'top', '6px');
  assertDeclaration(expandedToggle, 'bottom', 'auto');
  // Text gets top clearance so the pinned button doesn't sit on line 1.
  const expandedText = ruleBody(
    '.chat-sticky-prompt.is-expanded .chat-sticky-prompt-text',
  );
  assertDeclaration(expandedText, 'padding-top', '32px');
  // With a top-right jump-to-comment icon present, the toggle steps left.
  const withJump = ruleBody(
    '.chat-sticky-prompt.is-expanded.has-comment-jump .chat-sticky-prompt-expand',
  );
  assertDeclaration(withJump, 'right', '40px');
});

// ── jump-to-latest button ──────────────────────────────────────────────
//
// It shipped as a flat oval that scrolled away with the messages: "not
// floating, not round". Each assertion below pins one of the three causes,
// none of which was visible in the button's own rule — the rule said
// ``sticky`` and ``34px`` square and rendered as neither.

const uncommented = css.replace(/\/\*[\s\S]*?\*\//g, '');

// Every top-level rule in source order, one entry per comma-separated part.
function cascade() {
  const out = [];
  for (const m of uncommented.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    for (const selector of m[1].split(',')) {
      out.push({ selector: selector.trim(), body: m[2] });
    }
  }
  return out;
}

test('jump-to-latest stays sticky: no equal-ranked rule replaces its position', () => {
  // The button matches each of these single simple selectors, all (0,1,0).
  // A tie is settled by source ORDER, so the last one to declare ``position``
  // wins — which is how ``[data-tooltip] { position: relative }``, thousands
  // of lines further down, turned the sticky button into a relative one.
  // Asserting the WINNER, not that the rule exists: the rule always existed.
  const matching = new Set([
    '.event-log-scroll-bottom', '[data-tooltip]', '.tooltip-above', '.tooltip-end',
  ]);
  const declaring = cascade().filter(({ selector, body }) => (
    matching.has(selector) && /(?:^|;)\s*position\s*:/.test(body)
  ));
  assert.deepEqual(
    declaring.map((r) => r.selector), ['.event-log-scroll-bottom'],
    'a rule the jump-to-latest button also matches now sets ``position`` at '
    + 'the same specificity — it will out-rank the button\'s ``sticky`` by '
    + 'source order. Make that rule a ``:where()`` base instead',
  );
  assertDeclaration(ruleBody('.event-log-scroll-bottom'), 'position', 'sticky');
});

test('jump-to-latest is a circle that the flex column cannot squash', () => {
  const body = ruleBody('.event-log-scroll-bottom');
  // #event-log is an overflowing flex column: without this the button is
  // shrunk to its glyph's height (34x17) and a 50% radius draws an ellipse.
  assertDeclaration(body, 'flex', 'none');
  assertDeclaration(body, 'width', '28px');
  assertDeclaration(body, 'height', '28px');
  assertDeclaration(body, 'border-radius', '50%');
});

test('jump-to-latest floats just above the composer and costs no layout', () => {
  const body = ruleBody('.event-log-scroll-bottom');
  // A sticky inset is measured from the scrollport's CONTENT box, and
  // #event-log's padding-bottom already reserves the composer + queued list.
  // Repeating them here parked the button a composer's height too high.
  assertDeclaration(body, 'bottom', '0');
  assert.doesNotMatch(body, /--composer-h|--queued-h/);
  assert.ok(
    cascade().some(({ selector, body }) => (
      selector === '#event-log' && /padding-bottom\s*:[^;]*--composer-h/.test(body)
    )),
    'the log no longer reserves the composer in its padding — the button\'s '
    + '``bottom: 0`` relies on that',
  );
  // Height + the column gap, negated: mounting the button must not add to
  // the scroll height, or the log jumps when it appears and disappears.
  assertDeclaration(body, 'margin-top', 'calc\\(-1 \\* \\(28px \\+ 12px\\)\\)');
});
