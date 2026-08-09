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

// The three main-screen panels read as separated rounded cards. These
// pin the shell geometry: without the gutter (padding + column-gap) the
// cards touch and the rounded corners are invisible.
test('the top-tabs shell puts an 8px gutter around and between the panels', () => {
  const body = ruleBody('#layout.has-top-tabs');

  assertDeclaration(body, 'column-gap', '8px');
  assertDeclaration(body, 'padding', '0 8px 8px');
});

test('the tab strip and task header stay full-bleed above the cards', () => {
  // Negative horizontal margins cancel the shell padding; the header
  // also carries the vertical gutter down to the panel tops.
  assertDeclaration(ruleBody('#layout.has-top-tabs > #tabs-pane'), 'margin', '0 -8px');
  assertDeclaration(
    ruleBody('#layout.has-top-tabs > #task-header-slot'), 'margin', '0 -8px 8px',
  );
});

test('every panel is an outlined, rounded card', () => {
  // One shared rule for all three panels — see components/PanelCard.jsx.
  const body = ruleBody('.panel-card');

  assertDeclaration(body, 'border', '1px solid #2a2a2a');
  assertDeclaration(body, 'border-radius', '12px');
  // The resize handle anchors to the card, not the layout.
  assertDeclaration(body, 'position', 'relative');
});

test('panel content, not the card, clips to the rounded corners', () => {
  // Clipping on the card itself would cut off the resize handle, which
  // deliberately sits out in the gutter beside it.
  const body = ruleBody('.panel-card-content');

  assertDeclaration(body, 'overflow', 'hidden');
  assertDeclaration(body, 'border-radius', 'inherit');
  assertDeclaration(body, 'flex', '1');
});

test('the resize handles sit in the gutter between cards', () => {
  // One shared rule; the ids only say which card edge they hang off.
  const shared = ruleBody('.pane-resizer');

  assertDeclaration(shared, 'width', '10px');
  assertDeclaration(shared, 'cursor', 'col-resize');
  assertDeclaration(ruleBody('#left-pane-resizer'), 'right', '-9px');
  assertDeclaration(ruleBody('#right-pane-resizer'), 'left', '-9px');
});

test('the handle shows a dot grip that lights up on hover and drag', () => {
  // The grip is the "draggable" affordance; without it the gutter reads
  // as dead space. Dots are currentColor so the states only move color.
  const grip = ruleBody('.pane-resizer-grip');
  assertDeclaration(grip, 'color', '#737373');
  assertDeclaration(grip, 'background-image', 'radial-gradient\\(circle, currentColor[^;]*');

  const active = ruleBody(
    '.pane-resizer:hover .pane-resizer-grip,\n.pane-resizer.is-dragging .pane-resizer-grip',
  );
  assertDeclaration(active, 'color', '#0a84ff');
});

test('only the handle being dragged turns blue', () => {
  // The active visual keys off .is-dragging (one handle), NOT the global
  // .kato-resizing body class — that lit up the other pane's handle too.
  assertDeclaration(ruleBody('.pane-resizer.is-dragging::before'), 'background', '#0a84ff');

  const global = ruleBody('body.kato-resizing');
  assertDeclaration(global, 'cursor', 'col-resize');
  assert.doesNotMatch(
    css,
    /body\.kato-resizing\s+\.pane-resizer/,
    'kato-resizing must not paint any handle — it is document ergonomics only',
  );
});

test('the centre panes drop their own side dividers inside the card', () => {
  const body = ruleBody('.center-pane-with-tabs > .diff-pane');

  assertDeclaration(body, 'border-left', '0');
  assertDeclaration(body, 'border-right', '0');
});
