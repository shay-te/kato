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

test('SessionHeader can shrink inside the task pane', () => {
  const body = ruleBody('#session-header');
  assertDeclaration(body, 'min-width', '0');
  assertDeclaration(body, 'overflow', 'visible');
});

test('SessionHeader title row clips long summaries to one line', () => {
  const rowBody = ruleBody('.session-header-info');
  const summaryBody = ruleBody('#session-task-summary');

  assertDeclaration(rowBody, 'width', '100%');
  assertDeclaration(rowBody, 'min-width', '0');
  assertDeclaration(summaryBody, 'overflow', 'hidden');
  assertDeclaration(summaryBody, 'text-overflow', 'ellipsis');
  assertDeclaration(summaryBody, 'white-space', 'nowrap');
});

test('Chats menu rows override the global header icon-button skin', () => {
  // Scoped to the MENU, not ``header .chats-menu``. The chats button moved
  // out of the session header into the agent-tab strip, and the old
  // descendant selector then matched nothing — every row silently lost its
  // full-width 40px skin and rendered as a cramped icon button.
  assert.doesNotMatch(css, /header \.chats-menu button/);
  const body = ruleBody('.chats-menu button:not(.header-status)');

  assertDeclaration(body, 'width', '100%');
  assertDeclaration(body, 'height', 'auto');
  assertDeclaration(body, 'min-height', '40px');
  assertDeclaration(body, 'justify-content', 'flex-start');
  assertDeclaration(body, 'border', '0');
  assertDeclaration(body, 'border-radius', '0');
  assertDeclaration(body, 'padding', '8px 12px');
});

test('the chats menu opens away from the panel edge it sits on', () => {
  // The anchor has to match where the BUTTON is, and the button has moved
  // twice — session header (right), agent-tab strip (left), and now the
  // agent chat bar, whose space-between layout puts it back at the RIGHT.
  // Each time the anchor was left behind, the menu opened off-panel and the
  // operator saw a clipped popup.
  const body = ruleBody('.chats-menu');
  assertDeclaration(body, 'right', '0');
  assertDeclaration(body, 'left', 'auto');
});

test('the chats menu is clamped to the viewport at BOTH bounds', () => {
  // Independent of the anchor: whichever edge it grows from, it must fit.
  // The min-width matters as much as the max: when the two conflict CSS
  // resolves in favour of min-width, so a flat 420px floor would win over
  // the clamp and overflow a narrow viewport regardless.
  const body = ruleBody('.chats-menu');
  assert.match(body, /max-width:\s*min\(640px,/);
  assert.match(body, /min-width:\s*min\(420px,/);
});
