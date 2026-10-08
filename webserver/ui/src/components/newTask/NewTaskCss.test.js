// The "New task" pane and its tab, checked against the COMPILED stylesheet
// (a sass error writes a stub over app.css and exits 0, so the source alone
// proves nothing; jsdom has no layout to measure).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const css = readFileSync(
  join(new URL('..', import.meta.url).pathname, '../../../static/css/app.css'),
  'utf8',
);

function ruleBody(selector) {
  const start = css.indexOf(`\n${selector} {`);
  assert.notEqual(start, -1, `no rule for ${selector}`);
  return css.slice(start, css.indexOf('}', start));
}

test('the pane fills the space under the strip, like the review loop pane', () => {
  assert.match(css, /\.center-pane-with-tabs > #review-loop-pane,\n\.center-pane-with-tabs > #new-task-pane \{[^}]*flex: 1/);
  assert.match(css, /#review-loop-pane,\n#new-task-pane \{[^}]*display: flex/);
});

test('the description takes the height; settings sit in a narrow column', () => {
  assert.match(ruleBody('.new-task-body'), /grid-template-columns: minmax\(0, 1fr\) minmax\(260px, 320px\)/);
  assert.match(css, /\.new-task-description,\n\.new-task-preview \{[^}]*flex: 1/);
  assert.match(css, /@media \(max-width: 900px\) \{\s*\.new-task-body \{\s*grid-template-columns: minmax\(0, 1fr\)/);
});

test('its tab reads apart from file (cyan) and loop (indigo) tabs: green', () => {
  assert.match(ruleBody('.file-tab.is-new-task.active'), /border-bottom-color: #34d399/);
  assert.match(ruleBody('.file-tab-new-task-icon'), /color: #34d399/);
  assert.match(css, /\.file-tab\.is-review-loop\.is-group-start,\n\.file-tab\.is-new-task\.is-group-start \{/);
});
