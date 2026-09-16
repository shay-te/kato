// The built stylesheet has to BE a stylesheet.
//
// ``npm run build:css`` writes sass's output to ``static/css/app.css`` — and
// when sass fails, it writes a ~650-byte document whose whole content is a
// ``body::before { content: "Error: Undefined variable..." }`` stub, then
// exits 0. Every rule in the app disappears, the build reports success, and
// the UI comes back unstyled.
//
// That happened here: one undefined token ($C-NEUTRAL-100-A28) blanked the
// entire stylesheet, and the operator's report was that a separator they had
// asked for twice still was not there. The markup was right; the CSS was a
// stub.
//
// Every other *Css.test.js greps the built file for its own rule, so each of
// them fails in that state — but only if someone runs them, and each failure
// reads as "my rule is missing" rather than "the stylesheet is gone". This
// one names the real fault.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const CSS_PATH = new URL('../../../static/css/app.css', import.meta.url);
const css = readFileSync(CSS_PATH, 'utf8');

// The error stub is ~650 bytes; the real sheet is ~300KB. Anything in
// between is also wrong, so the floor is deliberately far above the stub and
// far below the real file.
const MIN_REAL_STYLESHEET_BYTES = 50_000;

test('the built stylesheet is not a sass error stub', () => {
  assert.doesNotMatch(
    css,
    /content:\s*"Error:/,
    'static/css/app.css holds a sass error stub — the build failed silently '
    + 'and every rule in the app is gone. Fix the SCSS and rebuild.',
  );
  assert.ok(
    css.length > MIN_REAL_STYLESHEET_BYTES,
    `static/css/app.css is only ${css.length} bytes — that is the failed-build `
    + 'stub, not the stylesheet.',
  );
});

test('long-standing rules survived the build', () => {
  // A sanity spread across the app: if these are missing the sheet is not
  // merely missing a new rule, it is broken.
  for (const selector of [
    '#session-header',
    '.session-header-actions',
    '.files-tab',
    '.settings-drawer-panel',
  ]) {
    assert.ok(
      css.includes(selector),
      `${selector} is missing from the built stylesheet`,
    );
  }
});

test('the toolbar separator is real and visible', () => {
  // Asked for twice. A rule that exists but paints nothing looks exactly like
  // no rule at all, so assert it HAS a background and a height.
  const match = css.match(/\.session-header-separator\s*\{([^}]*)\}/);
  assert.ok(match, '.session-header-separator is missing from the stylesheet');
  const body = match[1];
  assert.match(body, /background:\s*rgba?\(/, 'the separator paints nothing');
  assert.match(body, /height:\s*\d/, 'the separator has no height');
});

test('the settings icon picker marks the chosen icon', () => {
  assert.ok(
    css.includes('.settings-prompt-icon-check'),
    'the icon picker\'s selected-tile mark is missing from the stylesheet',
  );
});
