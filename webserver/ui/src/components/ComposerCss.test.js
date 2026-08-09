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
// that shapes the icon-only action buttons into 34px circles. Hovering
// the mode pill collapsed it to a circle, which moved it out from under
// the pointer; that un-hovered it, it expanded back under the pointer,
// and it flickered for as long as the cursor rested on it.
test('hovering a composer trigger never changes its box', () => {
  const shape = ruleBodyContaining('.composer-actions-trigger', 'border-radius: 50%');
  assertDeclaration(shape, 'width', '34px');

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
