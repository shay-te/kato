// Task tab names are shown capitalized ("admin admin" reads "Admin Admin") —
// in CSS only, so the stored name and every other copy of it keep exactly what
// was typed. The rename box must NOT be transformed: editing would show one
// spelling while saving another.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

function rules(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))]
    .map((match) => match[1]);
}

test('the task name on a tab is capitalized', () => {
  const bodies = rules('.tabs-pane-side .tab .tab-label-title');
  assert.ok(bodies.length > 0, 'expected the tab title rule in the built stylesheet');
  assert.ok(
    bodies.some((body) => /text-transform\s*:\s*capitalize\s*;/.test(body)),
    'the tab title is not capitalized',
  );
});

test('the rename box keeps what is typed', () => {
  for (const body of rules('.tabs-pane-side .tab .tab-label-rename')) {
    assert.doesNotMatch(body, /text-transform/);
  }
});
