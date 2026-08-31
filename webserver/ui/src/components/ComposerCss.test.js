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

// ``.composer-actions-trigger`` heads several rules; pick the one that
// actually carries the declaration under test.
function ruleBodyContaining(selector, marker) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const [, body] of css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))) {
    if (body.includes(marker)) { return body; }
  }
  assert.fail(`no ${selector} rule declares ${marker}`);
  return '';
}

// Regression: `.composer-mode-trigger:hover` was grouped with the rule
// that shapes the icon-only action buttons into circles. Hovering
// the mode pill collapsed it to a circle, which moved it out from under
// the pointer; that un-hovered it, it expanded back under the pointer,
// and it flickered for as long as the cursor rested on it.
test('hovering a composer trigger never changes its box', () => {
  const shape = ruleBodyContaining('.composer-actions-trigger', 'border-radius: 50%');
  assertDeclaration(shape, 'width', '28px');

  const boxProps = ['width', 'height', 'padding', 'margin', 'border-width', 'font-size'];
  // Comments stripped first — this file's own explanation names the
  // selector, and an unstripped scan matches the prose instead of a rule.
  const hoverRules = css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(
    /(\.composer-(?:mode|actions)-trigger:hover[^{]*)\{([^}]*)\}/g,
  );
  for (const [, selector, body] of hoverRules) {
    for (const prop of boxProps) {
      assert.doesNotMatch(
        body,
        new RegExp(`(^|;|\\s)${prop}\\s*:`),
        `${selector.trim()} sets ${prop} — a hover that resizes the control `
        + 'pulls it out from under the pointer and flickers',
      );
    }
  }
});

test('both composer triggers get hover feedback', () => {
  // The mode pill lost its hover colour when the stray :hover above was
  // sitting one selector group too high.
  const body = ruleBody(
    '.composer-mode-trigger:hover,\n.composer-actions-trigger:hover,'
    + '\n.composer-mode-trigger.is-open,\n.composer-actions-trigger.is-open',
  );
  assertDeclaration(body, 'color', '#f5f5f7');
  assertDeclaration(body, 'border-color', '#737373');
});

test('the composer capsule sits an even 8px inside the chat panel', () => {
  const body = ruleBody('#message-form');
  assertDeclaration(body, 'bottom', '8px');
  assertDeclaration(body, 'width', 'min\\(720px, 100% - 16px\\)');
});


// The composer must not change height when you start typing.
//
// An EMPTY composer is sized by MessageForm's SINGLE_LINE_TEXTAREA_HEIGHT;
// the moment there is text the auto-size hook switches to scrollHeight, which
// #message-input's min-height floors. One line of text measures 1.4em + 8px
// (4px padding top and bottom), so while these two disagreed the box dropped
// 8px on the first keystroke and grew back when the field was cleared —
// shifting the toolbar row under the pointer on every message.
test('the empty composer and the CSS floor are the SAME height', () => {
  // Read the constant as TEXT rather than importing it: this file runs under
  // ``node --test``, which cannot load .jsx.
  const source = readFileSync(
    new URL('./MessageForm.jsx', import.meta.url), 'utf8',
  );
  const declaredInJs = source.match(
    /SINGLE_LINE_TEXTAREA_HEIGHT\s*=\s*'([^']+)'/,
  );
  assert.ok(declaredInJs, 'expected MessageForm to declare the empty height');

  const body = ruleBody('#message-input');
  const declaredInCss = body.match(/min-height\s*:\s*([^;]+);/);
  assert.ok(declaredInCss, 'expected #message-input to declare a min-height');

  const normalise = (value) => value.replace(/\s+/g, '');
  assert.equal(
    normalise(declaredInCss[1]),
    normalise(declaredInJs[1]),
    'min-height must equal SINGLE_LINE_TEXTAREA_HEIGHT, or the box resizes '
    + 'on the first character typed',
  );
});
