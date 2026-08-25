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

test('the chats menu opens rightward from its button', () => {
  // It was ``right: 0`` — correct while the button sat at the right end of
  // the session header, wrong once it moved to the agent-tab strip at the
  // chat panel's LEFT edge: a 420px-wide menu anchored by its right edge
  // hung ~360px off-panel and the operator saw a clipped popup.
  const body = ruleBody('.chats-menu');
  assertDeclaration(body, 'left', '0');
  assertDeclaration(body, 'right', 'auto');
  assert.doesNotMatch(body, /right:\s*0;/);
});
